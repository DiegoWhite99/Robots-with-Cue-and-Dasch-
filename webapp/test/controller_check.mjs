// Prueba determinista de la lógica de alto nivel (controller.js) con un robot
// simulado (sin BLE): dead-reckoning, grabador y reproductor de rutas.
// Uso: node webapp/test/controller_check.mjs
import { Controller } from '../public/js/controller.js';
import { ROBOT_TYPE } from '../public/js/protocol.js';

let pass = 0, fail = 0;
function check(name, cond, extra = '') {
  if (cond) { pass++; console.log(`  OK   ${name}`); }
  else { fail++; console.log(`  FAIL ${name} ${extra}`); }
}

// ---- robot simulado: imita la superficie de WonderRobot ----
class FakeRobot {
  constructor() {
    this.connected = true;
    this.type = ROBOT_TYPE.DASH;
    this.typeName = 'Dash';
    this.name = 'TestBot';
    this.sensors = {};
    this.onSensors = null;
    this.onCommand = null;
    this.calls = []; // registro de comandos recibidos
  }
  _cmd(action, params, record) {
    this.calls.push({ action, params });
    if (record && this.onCommand) this.onCommand(action, params);
  }
  drive(l, a, record = true) { this._cmd('drive', { linear: l, angular: a }, record); }
  stop(record = true) { this._cmd('stop', {}, record); }
  head(p, t, record = true) { this._cmd('head', { pan: p, tilt: t }, record); }
  lights(r, g, b, record = true) { this._cmd('lights', { r, g, b }, record); }
  playSound() {}
  feed(sensors) { this.sensors = sensors; if (this.onSensors) this.onSensors(sensors); }
}

const robot = new FakeRobot();
const ctl = new Controller(robot);

// ============ 1) Dead-reckoning: avanzar recto ============
// Rumbo 0°, ambos encoders crecen igual -> debe avanzar en +X.
robot.feed({ pose: { deg: 0 }, encoderLeftCm: 0, encoderRightCm: 0, distFront: 80, distRear: 80 });
robot.feed({ pose: { deg: 0 }, encoderLeftCm: 10, encoderRightCm: 10, distFront: 80, distRear: 80 });
robot.feed({ pose: { deg: 0 }, encoderLeftCm: 20, encoderRightCm: 20, distFront: 80, distRear: 80 });
check('dead-reckoning avanza ~20cm en X', Math.abs(ctl.pose.x - 20) < 0.5, `x=${ctl.pose.x}`);
check('dead-reckoning sin deriva en Y', Math.abs(ctl.pose.y) < 0.5, `y=${ctl.pose.y}`);
check('traza acumula puntos', ctl.track.length >= 2, `len=${ctl.track.length}`);
check('mapa de calor con celdas', ctl.heat.size > 0, `size=${ctl.heat.size}`);

// Girar a rumbo 90° y avanzar -> debe moverse en +Y.
robot.feed({ pose: { deg: 90 }, encoderLeftCm: 30, encoderRightCm: 30, distFront: 80, distRear: 80 });
robot.feed({ pose: { deg: 90 }, encoderLeftCm: 40, encoderRightCm: 40, distFront: 80, distRear: 80 });
check('tras girar 90° avanza en +Y', ctl.pose.y > 9, `y=${ctl.pose.y}`);

// ============ 2) Grabador ============
ctl.recorder.start();
check('grabando activo', ctl.recorder.recording === true);
robot.drive(30, 0);             // record=true -> se graba
robot.drive(0, 90);
robot.lights(1, 0, 0);
robot.drive(15, 0, false);      // record=false (p.ej. explorador) -> NO se graba
const [events, dur] = ctl.recorder.stop();
check('graba solo comandos con record=true', events.length === 3, `n=${events.length}`);
check('primer evento es drive', events[0][1] === 'drive', JSON.stringify(events[0]));
check('duración >= 0', dur >= 0, `dur=${dur}`);

// ============ 3) Reproductor de rutas ============
robot.calls = [];
const eventsBeforeReplay = ctl.recorder.events.length;
const steps = [
  [0, 'drive', { linear: 20, angular: 0 }],
  [30, 'head', { pan: 10, tilt: 5 }],
  [60, 'lights', { r: 0, g: 1, b: 0 }],
  [90, 'stop', {}],
];
const okPlay = await ctl.playRoute(steps);
check('playRoute devuelve true', okPlay === true);
const driveCall = robot.calls.find(c => c.action === 'drive' && c.params.linear === 20);
const stopCall = robot.calls.find(c => c.action === 'stop');
check('replay envió drive(20,0)', !!driveCall);
check('replay envió head', robot.calls.some(c => c.action === 'head'));
check('replay envió lights', robot.calls.some(c => c.action === 'lights'));
check('replay terminó con stop', !!stopCall);
check('replay no graba eventos nuevos', ctl.recorder.events.length === eventsBeforeReplay,
  `antes=${eventsBeforeReplay} después=${ctl.recorder.events.length}`);
check('replay.playing vuelve a false', ctl.replay.playing === false);

// ============ 4) Explorador (config + decisión de giro) ============
check('perfil por defecto normal', ctl.exploreProfile === 'normal');
check('alias de perfil agresivo', ctl.setExploreProfile('aggressive') === 'agresivo');
ctl.setExploreProfile('normal');
const [turnLeft] = ctl._chooseExploreTurn(60, 10); // izq más despejado
check('elige girar a la izquierda si izq despejado', turnLeft === true);
const [turnLeft2] = ctl._chooseExploreTurn(10, 60); // der más despejado
check('elige girar a la derecha si der despejado', turnLeft2 === false);
const snap = ctl.exploreSnapshot();
check('snapshot trae perfil', snap.profile === 'normal');

console.log(`\n${pass} OK, ${fail} FAIL`);
process.exit(fail === 0 ? 0 : 1);
