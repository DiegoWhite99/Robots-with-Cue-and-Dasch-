// protocol.js — Puerto byte-a-byte del protocolo Wonder Workshop (Dash/Cue) a JavaScript.
//
// Origen Python: WonderPy/WonderPy/core/packet_data_converter.py,
//                WonderPy/WonderPy/components/*, WonderPy/WonderPy/util/wwMath.py
//
// Aquí vive: UUIDs BLE, codificador de comandos (encode) y decodificador de
// sensores (decode). Todo replicado fiel a la implementación reverse-engineered.

// ----------------------------- UUIDs BLE ----------------------------- //
// Web Bluetooth exige UUIDs en minúsculas.
export const SERVICE_DASH_DOT = 'af237777-879d-6186-1f49-deca0e85d9c1'; // dash y dot
export const SERVICE_CUE      = 'af237778-879d-6186-1f49-deca0e85d9c1'; // cue
export const WW_SERVICES      = [SERVICE_DASH_DOT, SERVICE_CUE];

export const CHAR_CMD     = 'af230002-879d-6186-1f49-deca0e85d9c1'; // canal de comandos
export const CHAR_SENSOR0 = 'af230003-879d-6186-1f49-deca0e85d9c1'; // sensores 0 (todos)
export const CHAR_SENSOR1 = 'af230006-879d-6186-1f49-deca0e85d9c1'; // sensores 1 (dash y cue)

export const ROBOT_TYPE = { DASH: 1001, DOT: 1002, CUE: 1003, UNKNOWN: 1000 };

const MAX_PACKET_LEN = 20;

// ----------------------------- helpers numéricos ----------------------------- //
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function toRad(deg) { return (deg * Math.PI) / 180.0; }

// round() de Python = "round half to even" (banker's). Lo replicamos para que
// los bytes salgan idénticos a la salida de Python.
function pyRound(x) {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff < 0.5) return f;
  if (diff > 0.5) return f + 1;
  return (f % 2 === 0) ? f : f + 1; // exactamente .5 -> al par
}

function scaleBrightness(val) {
  return pyRound(clamp(val, 0.0, 1.0) * 255.0);
}

// =====================================================================
//  CODIFICADOR DE COMANDOS  (Python: encode_cmd y _encode_*)
// =====================================================================

// Conducción continua — packet 0x25 (Python: _encode_linear_angular).
// Acel. por defecto = mismas que WWCommandBody (lin 50, ang 900).
export function encodeLinearAngular(linearCmS, angularDegS, linAccCmSS = 50.0, angAccDegSS = 900.0) {
  const LINEAR_VEL_SCALE = 10.0 / 2.0;     // 5.0
  const ANGULAR_VEL_SCALE = 1000.0 / 8.0;  // 125
  const LINEAR_ACC_SCALE = 1.0;
  const ANGULAR_ACC_SCALE = 1000.0 / 64.0; // 15.625

  const linVel = pyRound(clamp(linearCmS * LINEAR_VEL_SCALE, -750.0, 750.0));
  const angVel = pyRound(clamp(toRad(angularDegS) * ANGULAR_VEL_SCALE, -1000.0, 1000.0));
  const linAcc = pyRound(clamp(linAccCmSS * LINEAR_ACC_SCALE, 0.0, 1023.0));
  const angAcc = pyRound(clamp(toRad(angAccDegSS) * ANGULAR_ACC_SCALE, 0.0, 1023.0));

  return Uint8Array.of(
    0x25,
    linVel & 0xFF,
    angVel & 0xFF,
    ((angVel & 0x700) >> 5) | ((linVel >> 8) & 0x07),
    linAcc & 0xFF,
    angAcc & 0xFF,
    ((angAcc & 0x300) >> 6) | ((linAcc >> 8) & 0x03),
  );
}

// Pose relativa — packet 0x23 (Python: _encode_pose + compose_pose).
// mode/ease/direction/wrapTheta como enteros (igual que los envía la librería).
export function encodePose(xCm, yCm, degrees, time, mode, ease, direction, wrapTheta) {
  const POS_SCALE = 10.0;
  const x = clamp(xCm, -8192 / POS_SCALE, 8191 / POS_SCALE);
  const xEnc = pyRound(x * POS_SCALE);
  const y = clamp(yCm, -8192 / POS_SCALE, 8191 / POS_SCALE);
  const yEnc = pyRound(y * POS_SCALE);

  const THETA_SCALE = (Math.PI / 180.0) * 100.0;
  const theta = clamp(degrees, -2048 / THETA_SCALE, 2047 / THETA_SCALE);
  const thetaEnc = pyRound(theta * THETA_SCALE);

  const TIME_SCALE = 1000.0;
  const timeCmd = clamp(time, 0, 65535 / TIME_SCALE);
  const timeMs = pyRound(timeCmd * TIME_SCALE);

  const modeEnc = (mode === 5) ? 3 : (mode & 0x03);

  return Uint8Array.of(
    0x23,
    xEnc & 0xFF,
    yEnc & 0xFF,
    thetaEnc & 0xFF,
    (timeMs >> 8) & 0xFF,
    timeMs & 0xFF,
    ((xEnc >> 8) & 0x3F) | ((thetaEnc >> 2) & 0xC0),
    ((yEnc >> 8) & 0x3F) | ((thetaEnc >> 4) & 0xC0),
    (modeEnc << 6) | ((ease & 0x01) << 5) | ((wrapTheta & 0x01) << 4) | (direction & 0x0F),
  );
}

// do_forward(y_cm, speed): pose recto. coords_api_to_json_pos(0, y) = (y, 0).
// mode=RELATIVE_MEASURED(2), ease=1, dir=INFERRED(2), wrap=1.
export function encodeForward(cm, speedCmS) {
  const t = Math.abs(cm) / speedCmS;
  return encodePose(cm, 0, 0, t, 2, 1, 2, 1);
}

// do_turn(deg, speed): igual que la librería, que pasa mode=True(1), ease=2,
// dir=INFERRED(2), wrap=False(0) de forma posicional.
export function encodeTurn(deg, speedDegS) {
  const t = Math.abs(deg) / speedDegS;
  return encodePose(0, 0, deg, t, 1, 2, 2, 0);
}

// Servo de cabeza — Python: _servo_angle_bytes (big-endian s16, *-100).
function servoAngleBytes(angleDeg, minDeg, maxDeg) {
  let v = clamp(angleDeg, minDeg, maxDeg);
  v = pyRound(v * -100.0);
  const buf = new Uint8Array(2);
  new DataView(buf.buffer).setInt16(0, v, false); // big-endian
  return buf;
}

function concatBytes(...arrs) {
  let len = 0;
  for (const a of arrs) len += a.length;
  const out = new Uint8Array(len);
  let off = 0;
  for (const a of arrs) { out.set(a, off); off += a.length; }
  return out;
}

// Cabeza pan: 0x06 (coords_api_to_json_pan = identidad), clamp -120..120.
export function encodeHeadPan(panDeg) {
  return concatBytes(Uint8Array.of(0x06), servoAngleBytes(panDeg, -120, 120));
}

// Cabeza tilt: 0x07. coords_api_to_json_tilt(tilt) = -tilt, clamp json -24..7.
export function encodeHeadTilt(tiltDeg) {
  return concatBytes(Uint8Array.of(0x07), servoAngleBytes(-tiltDeg, -24, 7));
}

function rgbBytes(r, g, b) {
  return Uint8Array.of(scaleBrightness(r), scaleBrightness(g), scaleBrightness(b));
}

// Luces RGB por zona. Prefijos: pecho 0x03, oreja-izq 0x0b, oreja-der 0x0c, top 0x0d.
export function encodeRGBChest(r, g, b)   { return concatBytes(Uint8Array.of(0x03), rgbBytes(r, g, b)); }
export function encodeRGBEarLeft(r, g, b) { return concatBytes(Uint8Array.of(0x0b), rgbBytes(r, g, b)); }
export function encodeRGBEarRight(r, g, b){ return concatBytes(Uint8Array.of(0x0c), rgbBytes(r, g, b)); }
export function encodeRGBTop(r, g, b)     { return concatBytes(Uint8Array.of(0x0d), rgbBytes(r, g, b)); }

// Parlante — Python: encode_cmd para WW_COMMAND_SPEAKER (dos mensajes: volumen + archivo).
export function encodeSpeaker(name, volume = 1.0) {
  const vol = Math.trunc(clamp(volume, 0, 2.5) * 100.0); // int() = trunca
  const msgs = [Uint8Array.of(0x0e, vol)];
  if (!name || name.length === 0 || name.length > 15 || name === 'STOPSOUND') {
    msgs.push(Uint8Array.of(0x1a)); // stop sound
  } else {
    const ascii = new Uint8Array(name.length);
    for (let i = 0; i < name.length; i++) ascii[i] = name.charCodeAt(i) & 0x7F;
    msgs.push(concatBytes(Uint8Array.of(0x18), ascii));
  }
  return msgs;
}

// Empaqueta varios mensajes en paquetes <20 bytes (Python: bucle final de encode_cmd).
// Usamos first-fit con break para no duplicar un mensaje en varios paquetes.
export function packMessages(msgs) {
  const packets = [];
  for (const msg of msgs) {
    let fit = false;
    for (let i = 0; i < packets.length; i++) {
      if (packets[i].length + msg.length < MAX_PACKET_LEN) {
        packets[i] = concatBytes(packets[i], msg);
        fit = true;
        break;
      }
    }
    if (!fit) packets.push(msg);
  }
  return packets;
}

// =====================================================================
//  DECODIFICADOR DE SENSORES  (Python: dot_sensor_decode / dash_sensor_decode)
// =====================================================================

function bytesToS16(lo, hi) {
  let v = ((hi & 0xFF) << 8) | (lo & 0xFF);
  if (v & 0x8000) v -= 0x10000;
  return v;
}

function decode12bitPoseInt(highByte, lowByte, nibbleIsMsb = false) {
  let hn = nibbleIsMsb ? (highByte >> 4) : (highByte & 0xf);
  if (hn & 0x8) hn |= 0xF0;
  hn &= 0xFF;
  return bytesToS16(lowByte, hn);
}

function reflectToDistanceCm(refl) {
  const MIN_REFLECTANCE = 10.0, MAX_DISTANCE = 100.0;
  if (refl < MIN_REFLECTANCE) return MAX_DISTANCE;
  return 48.7536 * Math.exp(-refl / 6.19267) + 16.9606 * Math.exp(-refl / 54.453) + 6.05125;
}

class EncoderExtender {
  constructor() { this.prev = 0; this.overflow = 0; }
  unwrap(v) {
    const R = 2 ** 15 - 1;
    if (v - this.prev < -R) this.overflow += 1;
    else if (v - this.prev > R) this.overflow -= 1;
    this.prev = v;
    return this.overflow * R * 2.0 + v;
  }
}

const WHEEL_CIRCUMFERENCE_CM = Math.PI * 7.85;

// Decodifica el paquete SENSOR0 (común). Subconjunto útil (sin micrófono).
export function dotSensorDecode(b) {
  const res = {};
  res.timestamp = b[1] | ((b[0] & 0xff) << 8);
  res.accel = {
    x: decode12bitPoseInt(b[4], b[2], true) / 2047.0 * 2.0,
    y: decode12bitPoseInt(b[4], b[3]) / 2047.0 * 2.0,
    z: decode12bitPoseInt(b[5], b[6], true) / 2047.0 * 2.0,
  };
  res.buttonMain = (b[8] & 0x10) > 0;
  res.button1 = (b[8] & 0x20) > 0;
  res.button2 = (b[8] & 0x40) > 0;
  res.button3 = (b[8] & 0x80) > 0;
  res.pickedUp = (b[0xb] & 4) !== 0;
  res.bumpStall = (b[0xb] & 8) !== 0;
  res.soundPlaying = (b[0xb] & 2) !== 0;
  res.animationPlaying = (b[0xb] & 0x40) !== 0;
  return res;
}

// Decodifica usando SENSOR0 (packet1) + SENSOR1 (data). Específico Dash/Cue.
export function dashSensorDecode(packet1, data, extenders) {
  const res = {};
  const GYRO_SCALE = 500.0 * 0.017453292519943295 / 2047.0;
  res.gyro = {
    roll: decode12bitPoseInt(data[4], data[5]) * GYRO_SCALE,
    pitch: decode12bitPoseInt(data[4], data[3], true) * GYRO_SCALE,
    yaw: decode12bitPoseInt(data[0], data[2]) * GYRO_SCALE,
  };

  res.distFrontLeft = { refl: data[7], cm: reflectToDistanceCm(data[7]) };
  res.distFrontRight = { refl: data[6], cm: reflectToDistanceCm(data[6]) };
  res.distRear = { refl: data[8], cm: reflectToDistanceCm(data[8]) };

  const leftEnc = extenders.left.unwrap(bytesToS16(data[0xe], data[0xe + 1]));
  const rightEnc = extenders.right.unwrap(bytesToS16(data[0x10], data[0x10 + 1]));
  res.encoderLeftCm = leftEnc * WHEEL_CIRCUMFERENCE_CM / 1200.0;
  res.encoderRightCm = rightEnc * WHEEL_CIRCUMFERENCE_CM / 1200.0;

  const HEAD_ANGLE_SCALE = 360.0 / 6.283185307179586 / 100.0;
  const panTmp = (data[0x12] & 1) ? 0xf : 0;
  res.headPan = decode12bitPoseInt(panTmp, data[0x13]) * HEAD_ANGLE_SCALE;
  let tiltSigned = data[0x12] & 0xFF;
  if (tiltSigned & 0x80) tiltSigned -= 0x100; // int8 con signo
  const tiltTmp = tiltSigned >> 1;
  res.headTilt = tiltTmp * HEAD_ANGLE_SCALE * -1.0;

  // watermark (cuántos comandos de movimiento quedan en cola)
  const event1Type = packet1[0x13], event1Data = packet1[0x10];
  const event2Type = packet1[0xf], event2Data = packet1[0xc];
  let watermark = null;
  if (event1Type === 1) watermark = event1Data;
  else if (event2Type === 1) watermark = event2Data;

  const poseX = decode12bitPoseInt(data[9], data[10], true);
  const poseY = decode12bitPoseInt(data[9], data[11]);
  const poseTheta = bytesToS16(data[12], data[13]);
  res.pose = {
    x: poseX / 10.0,
    y: poseY / 10.0,
    deg: (poseTheta / 1000.0) * 180.0 / Math.PI,
    watermark,
  };
  return res;
}

export { EncoderExtender };
