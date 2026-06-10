// controller.js — Lógica de alto nivel (puerto de robot_core.Controller a JS).
//
// Envuelve a WonderRobot y añade: dead-reckoning (rumbo del giroscopio + encoders),
// mapa de calor, evita-obstáculos, explorador autónomo con anti-bucle, grabador de
// rutas y reproductor.

import { ROBOT_TYPE } from './protocol.js';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => performance.now() / 1000.0; // segundos monotónicos

// Colores por nombre -> (r,g,b) en 0..1 (puerto de robot_core.COLOR_RGB).
const COLOR_RGB = {
  red: [1, 0, 0], rojo: [1, 0, 0],
  green: [0, 1, 0], verde: [0, 1, 0],
  blue: [0, 0.3, 1], azul: [0, 0.3, 1],
  yellow: [1, 0.9, 0], amarillo: [1, 0.9, 0],
  magenta: [1, 0, 0.7], morado: [0.6, 0, 1], purple: [0.6, 0, 1],
  cyan: [0, 0.9, 1],
  white: [1, 1, 1], blanco: [1, 1, 1],
  off: [0, 0, 0], apagar: [0, 0, 0], negro: [0, 0, 0],
};

function clampNum(v, lo, hi) { v = Number(v); return Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : lo; }

// Sonidos de alerta por tipo de robot (clips de fábrica que viven en el robot).
const ALERT_SOUNDS = {
  [ROBOT_TYPE.CUE]: 'SNCHWHOA',
  [ROBOT_TYPE.DASH]: 'SYSTWOAH_NO',
  [ROBOT_TYPE.DOT]: 'SYSTWHOA',
};

// ----------------------------- grabador ----------------------------- //
class Recorder {
  constructor() {
    this.recording = false;
    this._t0 = null;
    this.events = []; // [[t_ms, action, params], ...]
  }
  start() { this.events = []; this._t0 = now(); this.recording = true; }
  stop() { this.recording = false; return [this.events.slice(), this._durationMs()]; }
  _durationMs() { return this.events.length ? this.events[this.events.length - 1][0] : 0; }
  elapsedMs() { return this._t0 === null ? 0 : Math.round((now() - this._t0) * 1000); }
  stepCount() { return this.events.length; }
  add(action, params) {
    if (!this.recording) return;
    const t = Math.round((now() - this._t0) * 1000);
    this.events.push([t, action, params]);
  }
}

export class Controller {
  // Perfiles de exploración — copiados verbatim de robot_core.EXPLORE_PROFILES.
  static EXPLORE_PROFILES = {
    suave: {
      speed: 18.0, warn_cm: 50.0, min_speed: 8.0, stuck_s: 4.6, critical_pad: 5.0,
      visit_soft: 4, visit_hard: 9, turn_speed: 118.0, turn_hold: 0.74,
      escape_front_cm: 16.0, escape_rear_cm: 16.0, loop_escape_extra_cm: 4.0,
      loop_turn_extra: 18.0, loop_turn_hold_extra: 0.20, stuck_escape_cm: 18.0,
      stuck_turn_speed: 132.0, stuck_turn_hold: 0.85, improve_delta: 5.0,
      improve_safe_cm: 26.0, bias_gain: 0.18, bias_penalty: 0.22, tick_s: 0.28,
    },
    normal: {
      speed: 25.0, warn_cm: 42.0, min_speed: 10.0, stuck_s: 3.2, critical_pad: 6.0,
      visit_soft: 4, visit_hard: 8, turn_speed: 120.0, turn_hold: 0.70,
      escape_front_cm: 18.0, escape_rear_cm: 18.0, loop_escape_extra_cm: 6.0,
      loop_turn_extra: 25.0, loop_turn_hold_extra: 0.30, stuck_escape_cm: 20.0,
      stuck_turn_speed: 150.0, stuck_turn_hold: 1.05, improve_delta: 6.0,
      improve_safe_cm: 30.0, bias_gain: 0.24, bias_penalty: 0.30, tick_s: 0.25,
    },
    agresivo: {
      speed: 34.0, warn_cm: 36.0, min_speed: 12.0, stuck_s: 2.2, critical_pad: 7.0,
      visit_soft: 3, visit_hard: 7, turn_speed: 132.0, turn_hold: 0.78,
      escape_front_cm: 22.0, escape_rear_cm: 22.0, loop_escape_extra_cm: 8.0,
      loop_turn_extra: 35.0, loop_turn_hold_extra: 0.36, stuck_escape_cm: 24.0,
      stuck_turn_speed: 165.0, stuck_turn_hold: 1.16, improve_delta: 7.0,
      improve_safe_cm: 32.0, bias_gain: 0.30, bias_penalty: 0.38, tick_s: 0.22,
    },
  };

  static DIST_CM = 25.0;
  static TURN_DEG = 90.0;
  static OBSTACLE_CM = 20.0;
  static AVOID_COOLDOWN = 2.5;
  static HEAT_BIN = 5.0;

  constructor(robot) {
    this.robot = robot;
    this.recorder = new Recorder();

    // sensores / evitar obstáculos
    this.avoidEnabled = false;
    this.avoiding = false;
    this.exploreEnabled = false;
    this.exploreProfile = 'normal';
    this.lastDistance = null;
    this.lastDistanceRear = null;
    this._avoidCooldown = 0.0;
    this.alert = { seq: 0, msg: '', distance: null, side: null };
    this._alertSeq = 0;

    // recorrido / mapa (dead-reckoning)
    this.track = [];
    this._lastTrackPt = null;
    this.pose = { x: 0, y: 0, deg: 0, valid: false };
    this.heat = new Map(); // "ix,iy" -> conteo
    this._drX = 0; this._drY = 0;
    this._prevEncL = null; this._prevEncR = null;
    this._deg0 = null;

    // estado del explorador con aprendizaje
    this.exploreAi = this._freshExploreAi();
    this._expLastProgressT = 0;
    this._expLastPose = null;

    // replay
    this._replayStop = false;
    this.replay = { playing: false, i: 0, total: 0, routeId: null };

    // agente IA
    this.agentBusy = false;
    this._agentStop = false;

    // wiring de sensores y grabación
    robot.onSensors = (s) => this._onSensors(s);
    robot.onCommand = (action, params) => this.recorder.add(action, params);
  }

  _freshExploreAi() {
    return {
      started_at: null, updated_at: null, escapes: 0, loops_avoided: 0,
      stuck_events: 0, turn_bias: 0.0, turn_left: 0, turn_right: 0,
      last_reason: '', last_decision: '', last_improved: null,
      last_before_cm: null, last_after_cm: null, recent: [],
    };
  }

  // ----------------------------- sensores ----------------------------- //
  _onSensors(s) {
    // dead-reckoning: rumbo del giroscopio (pose.deg) + distancia de encoders
    if (s.pose && s.encoderLeftCm != null && s.encoderRightCm != null) {
      const el = s.encoderLeftCm, er = s.encoderRightCm, deg = s.pose.deg;
      if (this._deg0 === null) this._deg0 = deg;
      if (this._prevEncL === null) { this._prevEncL = el; this._prevEncR = er; }
      const dC = ((el - this._prevEncL) + (er - this._prevEncR)) / 2.0;
      this._prevEncL = el; this._prevEncR = er;
      const heading = (deg - this._deg0) * Math.PI / 180.0;
      if (Math.abs(dC) < 50) {
        this._drX += dC * Math.cos(heading);
        this._drY += dC * Math.sin(heading);
      }
      const x = this._drX, y = this._drY;
      this.pose = { x: +x.toFixed(1), y: +y.toFixed(1), deg: +(deg - this._deg0).toFixed(1), valid: true };
      const lp = this._lastTrackPt;
      if (lp === null || (Math.abs(x - lp[0]) + Math.abs(y - lp[1])) >= 1.5) {
        this.track.push([+x.toFixed(1), +y.toFixed(1)]);
        this._lastTrackPt = [x, y];
        if (this.track.length > 2000) this.track.splice(0, this.track.length - 2000);
      }
      const cell = `${Math.floor(x / Controller.HEAT_BIN)},${Math.floor(y / Controller.HEAT_BIN)}`;
      if (this.heat.has(cell) || this.heat.size < 5000) {
        this.heat.set(cell, (this.heat.get(cell) || 0) + 1);
      }
    }

    // distancias
    const front = s.distFront, rear = s.distRear;
    const dleft = s.distFrontLeft, dright = s.distFrontRight;
    if (front != null) this.lastDistance = +front.toFixed(1);
    if (rear != null) this.lastDistanceRear = +rear.toFixed(1);

    if (!this.avoidEnabled || this.replay.playing) return;
    if (this.avoiding) return;
    const t = now();
    if ((t - this._avoidCooldown) <= Controller.AVOID_COOLDOWN) return;

    let threat = null; // [side, dist]
    if (front != null && front < Controller.OBSTACLE_CM) threat = ['front', front];
    if (rear != null && rear < Controller.OBSTACLE_CM) {
      if (threat === null || rear < threat[1]) threat = ['rear', rear];
    }
    if (threat === null) return;

    const [side, dist] = threat;
    this._avoidCooldown = t;
    this.avoiding = true;
    const sound = ALERT_SOUNDS[this.robot.type] || 'SNCHWHOA';
    this.robot.playSound(sound);
    this._alertSeq += 1;
    this.alert = {
      seq: this._alertSeq,
      msg: `¡Obstáculo ${side === 'front' ? 'al frente' : 'atrás'} a ${Math.round(dist)} cm! Freno y ajusto.`,
      distance: Math.round(dist), side,
    };
    if (this.exploreEnabled) {
      this._doEscapeLearning(side, dleft, dright, `obstaculo_${side}`);
    } else {
      const turnLeft = (dleft != null ? dleft : 0) >= (dright != null ? dright : 0);
      this._doEscape(side, turnLeft);
    }
  }

  // ----------------------------- mapa / helpers ----------------------------- //
  _exploreNote(msg) {
    const ts = new Date().toTimeString().slice(0, 8);
    const rec = this.exploreAi.recent.slice();
    rec.push(`${ts} · ${msg}`);
    this.exploreAi.recent = rec.slice(-20);
    this.exploreAi.updated_at = new Date().toISOString().slice(0, 19);
  }

  _poseCell() {
    const p = this.pose;
    if (!p.valid) return [null, 0];
    const ix = Math.floor(p.x / Controller.HEAT_BIN), iy = Math.floor(p.y / Controller.HEAT_BIN);
    return [[ix, iy], this.heat.get(`${ix},${iy}`) || 0];
  }

  _predictCellVisits(turnLeft) {
    const p = this.pose;
    if (!p.valid) return 0;
    const heading = (p.deg + (turnLeft ? 42.0 : -42.0)) * Math.PI / 180.0;
    const x = p.x + Math.cos(heading) * 26.0;
    const y = p.y + Math.sin(heading) * 26.0;
    return this.heat.get(`${Math.floor(x / Controller.HEAT_BIN)},${Math.floor(y / Controller.HEAT_BIN)}`) || 0;
  }

  _chooseExploreTurn(dleft, dright) {
    const leftClear = dleft != null ? dleft : (this.lastDistance || 30.0);
    const rightClear = dright != null ? dright : (this.lastDistance || 30.0);
    const leftVis = this._predictCellVisits(true);
    const rightVis = this._predictCellVisits(false);
    const bias = this.exploreAi.turn_bias;
    const leftScore = leftClear - (leftVis * 1.4) + (bias * 4.0);
    const rightScore = rightClear - (rightVis * 1.4) - (bias * 4.0);
    return [leftScore >= rightScore, {
      left_score: +leftScore.toFixed(2), right_score: +rightScore.toFixed(2),
      left_visits: leftVis, right_visits: rightVis,
    }];
  }

  _registerExploreEscape(reason, turnLeft, improved, beforeCm, afterCm, extra) {
    const [cell, visits] = this._poseCell();
    const cfg = this._exploreCfg();
    const ai = this.exploreAi;
    ai.escapes += 1;
    if (reason === 'stuck') ai.stuck_events += 1;
    if (visits >= cfg.visit_hard) ai.loops_avoided += 1;
    if (turnLeft) ai.turn_left += 1; else ai.turn_right += 1;
    const delta = improved ? cfg.bias_gain : -cfg.bias_penalty;
    ai.turn_bias = Math.max(-3.0, Math.min(3.0, ai.turn_bias + (turnLeft ? delta : -delta)));
    ai.last_reason = reason;
    ai.last_decision = turnLeft ? 'left' : 'right';
    ai.last_improved = improved;
    ai.last_before_cm = beforeCm == null ? null : +(+beforeCm).toFixed(1);
    ai.last_after_cm = afterCm == null ? null : +(+afterCm).toFixed(1);
    const cellTxt = cell ? `celda ${cell} visitas=${visits}` : 'celda sin pose';
    let msg = `escape ${reason} -> ${turnLeft ? 'izq' : 'der'} (${improved ? 'mejoró' : 'sin mejora'}), ${cellTxt}`;
    if (extra) msg += ` [L${extra.left_visits}/R${extra.right_visits}]`;
    this._exploreNote(msg);
  }

  exploreSnapshot() {
    const snap = JSON.parse(JSON.stringify(this.exploreAi));
    snap.running = this.exploreEnabled;
    snap.profile = this.exploreProfile;
    const [cell, visits] = this._poseCell();
    snap.cell = cell ? { ix: cell[0], iy: cell[1], visits } : null;
    return snap;
  }

  resetExploreLearning(clearMap = false) {
    const started = this.exploreAi.started_at;
    this.exploreAi = this._freshExploreAi();
    this.exploreAi.started_at = this.exploreEnabled ? started : null;
    this.exploreAi.updated_at = new Date().toISOString().slice(0, 19);
    this._expLastProgressT = now();
    this._expLastPose = null;
    if (clearMap) { this.clearTrack(); this._exploreNote('aprendizaje reiniciado y mapa limpiado.'); }
    else this._exploreNote('aprendizaje reiniciado.');
    return this.exploreSnapshot();
  }

  setAvoid(on) { this.avoidEnabled = !!on; return this.avoidEnabled; }

  _exploreCfg() {
    return { ...(Controller.EXPLORE_PROFILES[this.exploreProfile] || Controller.EXPLORE_PROFILES.normal) };
  }

  setExploreProfile(mode) {
    const aliases = {
      suave: 'suave', soft: 'suave', safe: 'suave',
      normal: 'normal', medio: 'normal', balanced: 'normal',
      agresivo: 'agresivo', agresiva: 'agresivo', aggressive: 'agresivo',
    };
    const chosen = aliases[(mode || '').trim().toLowerCase()];
    if (!chosen) return this.exploreProfile;
    this.exploreProfile = chosen;
    this._exploreNote(`perfil de exploración: ${chosen}.`);
    return this.exploreProfile;
  }

  setExplore(on) {
    on = !!on;
    if (on && !this.exploreEnabled) {
      this.exploreEnabled = true;
      this.avoidEnabled = true; // explorar SIEMPRE con anti-choque
      this._expLastProgressT = now();
      this._expLastPose = null;
      this.exploreAi.started_at = new Date().toISOString().slice(0, 19);
      this.exploreAi.updated_at = this.exploreAi.started_at;
      this._exploreNote(`explorador inteligente activado (${this.exploreProfile}).`);
      this._exploreLoop();
    } else if (!on) {
      this.exploreEnabled = false;
      this.robot.stop(false);
      this._exploreNote('explorador detenido.');
    }
    return this.exploreEnabled;
  }

  async _exploreLoop() {
    const updateProgress = (nowS) => {
      const p = this.pose;
      if (!p.valid) return false;
      const cur = [p.x, p.y];
      if (this._expLastPose === null) { this._expLastPose = cur; this._expLastProgressT = nowS; return true; }
      const moved = Math.abs(cur[0] - this._expLastPose[0]) + Math.abs(cur[1] - this._expLastPose[1]);
      this._expLastPose = cur;
      if (moved >= 1.2) { this._expLastProgressT = nowS; return true; }
      return false;
    };

    while (this.exploreEnabled && this.robot.connected) {
      const cfg = this._exploreCfg();
      if (this.replay.playing) { await sleep(180); continue; }
      if (this.avoiding) { await sleep(120); continue; }

      const t = now();
      updateProgress(t);
      if (t - this._expLastProgressT > cfg.stuck_s) {
        this.avoiding = true;
        this._doEscapeLearning('front', null, null, 'stuck');
        await sleep(150); continue;
      }

      const front = this.lastDistance != null ? this.lastDistance : 100.0;
      if (front <= (Controller.OBSTACLE_CM + cfg.critical_pad)) {
        this.avoiding = true;
        this._doEscapeLearning('front', null, null, 'frente_critico');
        await sleep(150); continue;
      }

      const [cell, visits] = this._poseCell();
      let speed = cfg.speed;
      if (front < cfg.warn_cm) speed = Math.min(speed, Math.max(cfg.min_speed, front * 0.65));
      if (visits > cfg.visit_soft) {
        speed = Math.max(cfg.min_speed, speed - Math.min(12.0, (visits - cfg.visit_soft) * 1.8));
      }
      if (cell && visits >= cfg.visit_hard) {
        this._exploreNote(`zona muy repetida ${cell} (visitas=${visits}), buscando salida.`);
      }
      this.robot.drive(speed, 0, false);
      await sleep(cfg.tick_s * 1000);
    }
    this.robot.stop(false);
  }

  async _doEscape(side, turnLeft) {
    const hold = (s) => sleep(s * 1000);
    try {
      this.robot.stop(false); await hold(0.30);
      this.robot.stop(false); await hold(0.10);
      const away = side === 'front' ? -18 : 18;
      this.robot.drive(away, 0, false); await hold(0.5);
      this.robot.drive(0, turnLeft ? 120 : -120, false); await hold(0.7);
      this.robot.stop(false);
    } catch (e) { console.error('[avoid] escape:', e); }
    finally { this.avoiding = false; }
  }

  async _doEscapeLearning(side, dleft, dright, reason) {
    const hold = (s) => sleep(s * 1000);
    const cfg = this._exploreCfg();
    const before = this.lastDistance;
    const [turnLeft, extra] = this._chooseExploreTurn(dleft, dright);
    const [, visits] = this._poseCell();
    let escapeCm = side === 'front' ? -cfg.escape_front_cm : cfg.escape_rear_cm;
    let turnSpeed = cfg.turn_speed;
    let turnHold = cfg.turn_hold;
    if (visits >= cfg.visit_hard) {
      const extraCm = cfg.loop_escape_extra_cm;
      escapeCm = side === 'front' ? (escapeCm - extraCm) : (escapeCm + extraCm);
      turnSpeed += cfg.loop_turn_extra;
      turnHold += cfg.loop_turn_hold_extra;
    }
    if (reason === 'stuck') {
      escapeCm = -cfg.stuck_escape_cm;
      turnSpeed = cfg.stuck_turn_speed;
      turnHold = cfg.stuck_turn_hold;
    }
    try {
      this.robot.stop(false); await hold(0.28);
      this.robot.stop(false); await hold(0.10);
      this.robot.drive(escapeCm, 0, false); await hold(Math.abs(escapeCm) < 22 ? 0.55 : 0.72);
      this.robot.drive(0, turnLeft ? turnSpeed : -turnSpeed, false); await hold(turnHold);
      this.robot.stop(false); await hold(0.10);
      const after = this.lastDistance;
      let improved = false;
      if (after != null) {
        if (before == null) improved = after > cfg.improve_safe_cm;
        else improved = (after > (before + cfg.improve_delta)) || (after > cfg.improve_safe_cm);
      }
      this._registerExploreEscape(reason, turnLeft, improved, before, after, extra);
    } catch (e) {
      console.error('[explore] escape:', e);
      this._registerExploreEscape(reason, turnLeft, false, before, this.lastDistance, extra);
    } finally { this.avoiding = false; }
  }

  // ----------------------------- recorrido / mapa ----------------------------- //
  trackSnapshot() {
    const heat = [];
    for (const [k, c] of this.heat.entries()) {
      const [ix, iy] = k.split(',').map(Number);
      heat.push([ix, iy, c]);
    }
    return { trail: this.track.slice(), heat, bin: Controller.HEAT_BIN, current: { ...this.pose } };
  }

  clearTrack() {
    this.track = []; this._lastTrackPt = null; this.heat = new Map();
    this._drX = 0; this._drY = 0; this._prevEncL = null; this._prevEncR = null; this._deg0 = null;
  }

  // ----------------------------- rutas (replay) ----------------------------- //
  async playRoute(steps) {
    if (this.replay.playing || !this.robot.connected || !steps || !steps.length) return false;
    this._replayStop = false;
    this.replay = { playing: true, i: 0, total: steps.length, routeId: null };
    const start = now();
    try {
      for (let i = 0; i < steps.length; i++) {
        const [tOff, action, params] = steps[i];
        while (!this._replayStop) {
          if ((now() - start) * 1000.0 >= tOff) break;
          await sleep(5);
        }
        if (this._replayStop) break;
        if (action === 'drive') this.robot.drive(params.linear, params.angular, false);
        else if (action === 'stop') this.robot.stop(false);
        else if (action === 'head') this.robot.head(params.pan, params.tilt, false);
        else if (action === 'lights') this.robot.lights(params.r, params.g, params.b, false);
        this.replay.i = i + 1;
      }
    } finally {
      this.robot.stop(false);
      this.replay.playing = false;
    }
    return true;
  }

  stopReplay() { this._replayStop = true; }

  // ----------------------------- baile (MotionRunner.dance) ----------------------------- //
  async dance() {
    const colors = [[1, 0, 0], [1, 0.6, 0], [1, 1, 0], [0, 1, 0], [0, 0.4, 1], [0.6, 0, 1]];
    for (let i = 0; i < 6; i++) {
      if (this._agentStop) return;
      this.robot.lights(...colors[i % colors.length], false);
      this.robot.drive(0, i % 2 === 0 ? 180 : -180, false);
      await sleep(450);
    }
    this.robot.stop(false);
    this.robot.lights(0, 0, 0, false);
  }

  // ----------------------------- agente IA (robot_core._run_plan) ----------------------------- //
  stopAgent() { this._agentStop = true; }

  async runPlan(steps) {
    if (!steps || !steps.length || !this.robot.connected || this.agentBusy) return false;
    this._agentStop = false;
    this.agentBusy = true;
    // ejecuta sin bloquear al llamador
    this._runPlanAsync(steps).finally(() => {
      this.robot.stop(false);
      this.agentBusy = false;
    });
    return true;
  }

  async _waitChecking(seconds) {
    const t0 = now();
    while (now() - t0 < seconds) {
      if (this._agentStop) return;
      await sleep(50);
    }
  }

  async _runPlanAsync(steps) {
    for (const st of steps) {
      if (this._agentStop) break;
      if (!st || typeof st !== 'object') continue;
      const act = String(st.action || '').toLowerCase();

      if (['forward', 'avanzar', 'adelante', 'walk', 'move'].includes(act)) {
        const cm = clampNum(st.cm ?? st.distance ?? 30, 1, 200);
        this.robot.forward(cm, 35);
        await this._waitChecking(cm / 35.0);
      } else if (['backward', 'back', 'retroceder', 'reverse', 'atras'].includes(act)) {
        const cm = clampNum(st.cm ?? st.distance ?? 30, 1, 200);
        this.robot.forward(-cm, 35);
        await this._waitChecking(cm / 35.0);
      } else if (['turn', 'girar', 'turnleft', 'turnright', 'rotate'].includes(act)) {
        let deg = Math.abs(clampNum(st.deg ?? st.degrees ?? 90, 1, 360));
        const d = String(st.dir || '').toLowerCase();
        if (act.includes('right') || ['right', 'derecha', 'der', 'r'].includes(d)) deg = -deg;
        this.robot.turn(deg, 90);
        await this._waitChecking(Math.abs(deg) / 90.0);
      } else if (['lights', 'light', 'luz', 'color'].includes(act)) {
        const rgb = COLOR_RGB[String(st.color || 'white').toLowerCase()] || [1, 1, 1];
        this.robot.lights(...rgb);
      } else if (act === 'head') {
        const pos = String(st.pos || 'center').toLowerCase();
        const tilt = (pos.includes('up') || pos.includes('arriba')) ? 20
          : (pos.includes('down') || pos.includes('abajo')) ? -8 : 0;
        this.robot.head(0, tilt);
      } else if (['dance', 'baila', 'bailar'].includes(act)) {
        await this.dance();
      } else if (['sound', 'voz', 'voice', 'audio'].includes(act)) {
        const slot = st.slot;
        if (slot != null) {
          const n = parseInt(slot, 10);
          if (n >= 1 && n <= 10) this.robot.playSound(`SYSTVOICE${n - 1}`);
        } else if (st.name) {
          this.robot.playSound(String(st.name));
        }
        await this._waitChecking(1.0);
      } else if (['wait', 'esperar', 'pausa', 'pause'].includes(act)) {
        await this._waitChecking(clampNum(st.seconds ?? st.s ?? 1, 0, 5));
      } else if (['stop', 'parar', 'alto', 'detener'].includes(act)) {
        this.robot.stop(false);
      }
    }
  }

  status() {
    return {
      connected: this.robot.connected,
      robot: this.robot.connected ? `${this.robot.typeName} «${this.robot.name}»` : null,
      recording: this.recorder.recording,
      rec_ms: this.recorder.recording ? this.recorder.elapsedMs() : 0,
      rec_steps: this.recorder.recording ? this.recorder.stepCount() : 0,
      replay: { ...this.replay },
      avoid: this.avoidEnabled,
      avoiding: this.avoiding,
      explore: this.exploreEnabled,
      explore_profile: this.exploreProfile,
      distance: this.lastDistance,
      distance_rear: this.lastDistanceRear,
      alert: { ...this.alert },
    };
  }
}
