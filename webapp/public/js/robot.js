// robot.js — Cliente Web Bluetooth para Dash / Cue.
//
// Equivalente en el navegador de WonderPy + robot_core.RobotBridge: conecta por
// BLE directo (navigator.bluetooth), envía comandos y decodifica sensores.
//
// Requisitos del navegador: Chrome/Edge (Android, Windows, macOS, Linux), contexto
// seguro (HTTPS o localhost). NO funciona en iPhone/iPad ni Firefox.

import {
  WW_SERVICES, SERVICE_CUE, SERVICE_DASH_DOT,
  CHAR_CMD, CHAR_SENSOR0, CHAR_SENSOR1, ROBOT_TYPE,
  encodeLinearAngular, encodeForward, encodeTurn,
  encodeHeadPan, encodeHeadTilt,
  encodeRGBChest, encodeRGBEarLeft, encodeRGBEarRight, encodeRGBTop,
  encodeSpeaker, packMessages,
  dotSensorDecode, dashSensorDecode, EncoderExtender,
} from './protocol.js';

// Aceleraciones (igual que robot_core.py).
const DRIVE_ACCEL_LIN = 120.0;
const STOP_ACCEL_LIN = 320.0;

export class WonderRobot {
  constructor() {
    this.device = null;
    this.server = null;
    this.cmdChar = null;
    this.type = ROBOT_TYPE.UNKNOWN;
    this.typeName = '?';
    this.name = '';
    this.connected = false;

    this.onSensors = null;     // callback(sensors) en cada paquete
    this.onConnect = null;
    this.onDisconnect = null;
    this.onCommand = null;     // callback(action, params) tras un envío real (para grabar)

    this.sensors = {
      distFront: null, distFrontLeft: null, distFrontRight: null, distRear: null,
      pose: null, gyro: null, head: null,
      encoderLeftCm: null, encoderRightCm: null,
      buttons: { main: false, b1: false, b2: false, b3: false },
      ts: 0,
    };

    this._extenders = { left: new EncoderExtender(), right: new EncoderExtender() };
    this._packet1 = null;        // SENSOR0 guardado a la espera de SENSOR1
    this._expectPacket2 = true;  // dash/cue mandan 2 paquetes

    // dedupe (igual que RobotBridge) para no inundar el enlace BLE
    this._lastVel = null;
    this._lastHead = null;
    this._lastLight = null;

    // cola de escritura: una operación GATT a la vez
    this._writeChain = Promise.resolve();
  }

  // ----------------------------- conexión ----------------------------- //
  async connect(typePref = 'any') {
    const filters = [];
    if (typePref === 'cue') filters.push({ services: [SERVICE_CUE] });
    else if (typePref === 'dash' || typePref === 'dot') filters.push({ services: [SERVICE_DASH_DOT] });
    else { filters.push({ services: [SERVICE_DASH_DOT] }); filters.push({ services: [SERVICE_CUE] }); }

    this.device = await navigator.bluetooth.requestDevice({
      filters,
      optionalServices: WW_SERVICES,
    });

    this.device.addEventListener('gattserverdisconnected', () => this._handleDisconnect());
    this.name = this.device.name || '';

    this.server = await this.device.gatt.connect();
    await new Promise(r => setTimeout(r, 500)); // dejar asentar el enlace

    // Detectar tipo por el servicio presente (D2 = Cue; D1 = Dash/Dot).
    const services = await this.server.getPrimaryServices();
    const svcUuids = services.map(s => s.uuid);
    if (svcUuids.includes(SERVICE_CUE)) { this.type = ROBOT_TYPE.CUE; this.typeName = 'Cue'; }
    else { this.type = ROBOT_TYPE.DASH; this.typeName = 'Dash'; }

    this.cmdChar = await this._findChar(services, CHAR_CMD);
    if (!this.cmdChar) throw new Error('No se encontró la característica de comandos.');

    await this._subscribeSensor(services, CHAR_SENSOR0);
    if (this._expectPacket2) await this._subscribeSensor(services, CHAR_SENSOR1);

    this.connected = true;
    if (this.onConnect) this.onConnect(this);
    return { type: this.typeName, name: this.name };
  }

  async _findChar(services, charUuid) {
    for (const svc of services) {
      try { return await svc.getCharacteristic(charUuid); }
      catch (_) { /* no está en este servicio */ }
    }
    return null;
  }

  async _subscribeSensor(services, charUuid) {
    const ch = await this._findChar(services, charUuid);
    if (!ch) return;
    try {
      await ch.startNotifications();
      ch.addEventListener('characteristicvaluechanged', (e) => this._onSensorData(charUuid, e.target.value));
    } catch (e) {
      console.warn('[BLE] sensores', charUuid, 'no disponibles:', e);
    }
  }

  _onSensorData(charUuid, dataView) {
    const b = new Uint8Array(dataView.buffer, dataView.byteOffset, dataView.byteLength);
    let decoded = null;

    if (charUuid === CHAR_SENSOR0) {
      if (!this._expectPacket2) decoded = dotSensorDecode(b);
      else this._packet1 = b; // esperar SENSOR1
    } else { // SENSOR1
      if (this._packet1) {
        decoded = dotSensorDecode(this._packet1);
        Object.assign(decoded, dashSensorDecode(this._packet1, b, this._extenders));
        this._packet1 = null;
      }
    }

    if (decoded) {
      this._applySensors(decoded);
      if (this.onSensors) {
        try { this.onSensors(this.sensors); } catch (err) { console.error('[sensors]', err); }
      }
    }
  }

  _applySensors(d) {
    const s = this.sensors;
    s.ts = performance.now();
    if (d.distFrontLeft && d.distFrontRight) {
      s.distFrontLeft = d.distFrontLeft.cm;
      s.distFrontRight = d.distFrontRight.cm;
      s.distFront = Math.min(d.distFrontLeft.cm, d.distFrontRight.cm);
    }
    if (d.distRear) s.distRear = d.distRear.cm;
    if (d.pose) s.pose = d.pose;
    if (d.gyro) s.gyro = d.gyro;
    if (typeof d.encoderLeftCm === 'number') s.encoderLeftCm = d.encoderLeftCm;
    if (typeof d.encoderRightCm === 'number') s.encoderRightCm = d.encoderRightCm;
    if (typeof d.headPan === 'number') s.head = { pan: d.headPan, tilt: d.headTilt };
    if (typeof d.buttonMain === 'boolean') {
      s.buttons = { main: d.buttonMain, b1: d.button1, b2: d.button2, b3: d.button3 };
    }
  }

  _handleDisconnect() {
    this.connected = false;
    this.cmdChar = null;
    if (this.onDisconnect) this.onDisconnect(this);
  }

  disconnect() {
    if (this.device && this.device.gatt.connected) this.device.gatt.disconnect();
  }

  // ----------------------------- escritura ----------------------------- //
  _write(bytes) {
    // Encadena las escrituras para no solapar operaciones GATT.
    this._writeChain = this._writeChain.then(async () => {
      const ch = this.cmdChar;
      if (!ch) return;
      try {
        if (ch.writeValueWithoutResponse) await ch.writeValueWithoutResponse(bytes);
        else await ch.writeValue(bytes);
      } catch (e) {
        console.warn('[BLE] write falló:', e);
      }
    });
    return this._writeChain;
  }

  _writePackets(msgs) {
    for (const pkt of packMessages(msgs)) this._write(pkt);
  }

  // ----------------------------- comandos ----------------------------- //
  // `record` (por defecto true) imita el parámetro `record` de robot_core: los
  // movimientos del explorador/replay/avoid lo pasan en false para no grabarse.
  drive(linearCmS, angularDegS, record = true) {
    if (!this.connected) return;
    const vel = [Math.round(linearCmS * 100) / 100, Math.round(angularDegS * 100) / 100];
    if (this._lastVel && vel[0] === this._lastVel[0] && vel[1] === this._lastVel[1]) return;
    this._lastVel = vel;
    this._write(encodeLinearAngular(linearCmS, angularDegS, DRIVE_ACCEL_LIN));
    if (record && this.onCommand) this.onCommand('drive', { linear: vel[0], angular: vel[1] });
  }

  stop(record = true) {
    if (!this.connected) return;
    if (this._lastVel && this._lastVel[0] === 0 && this._lastVel[1] === 0) return;
    this._lastVel = [0, 0];
    this._write(encodeLinearAngular(0, 0, STOP_ACCEL_LIN));
    if (record && this.onCommand) this.onCommand('stop', {});
  }

  head(panDeg, tiltDeg, record = true) {
    if (!this.connected) return;
    const pan = Math.max(-120, Math.min(120, panDeg));
    const tilt = Math.max(-10, Math.min(22, tiltDeg));
    const key = [Math.round(pan * 10) / 10, Math.round(tilt * 10) / 10];
    if (this._lastHead && key[0] === this._lastHead[0] && key[1] === this._lastHead[1]) return;
    this._lastHead = key;
    this._writePackets([encodeHeadPan(pan), encodeHeadTilt(tilt)]);
    if (record && this.onCommand) this.onCommand('head', { pan: key[0], tilt: key[1] });
  }

  lights(r, g, b, record = true) {
    if (!this.connected) return;
    const key = [Math.round(r * 1000) / 1000, Math.round(g * 1000) / 1000, Math.round(b * 1000) / 1000];
    if (this._lastLight && key.every((v, i) => v === this._lastLight[i])) return;
    this._lastLight = key;
    const msgs = [encodeRGBChest(r, g, b), encodeRGBEarLeft(r, g, b), encodeRGBEarRight(r, g, b)];
    if (this.type === ROBOT_TYPE.CUE) msgs.push(encodeRGBTop(r, g, b));
    this._writePackets(msgs);
    if (record && this.onCommand) this.onCommand('lights', { r: key[0], g: key[1], b: key[2] });
  }

  forward(cm, speedCmS = 35) {
    if (!this.connected) return;
    this._lastVel = null; // pose invalida el último estado de velocidad continua
    this._write(encodeForward(cm, speedCmS));
  }

  turn(deg, speedDegS = 90) {
    if (!this.connected) return;
    this._lastVel = null;
    this._write(encodeTurn(deg, speedDegS));
  }

  playSound(name, volume = 1.0) {
    if (!this.connected || !name) return;
    this._writePackets(encodeSpeaker(name, volume));
  }
}

export function webBluetoothAvailable() {
  return typeof navigator !== 'undefined' && !!navigator.bluetooth;
}
