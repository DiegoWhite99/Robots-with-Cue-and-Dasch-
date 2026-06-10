// Genera los mismos bytes con el codificador JS y los compara contra ref_python.py.
// Uso: node webapp/test/check_js.mjs
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  encodeLinearAngular, encodeForward, encodeTurn,
  encodeRGBChest, encodeHeadPan, encodeHeadTilt, encodeSpeaker, packMessages,
} from '../public/js/protocol.js';

const __dirname = dirname(fileURLToPath(import.meta.url));

const hex = (u8) => Buffer.from(u8).toString('hex');
const pkts = (...msgs) => packMessages(msgs).map(hex);

const js = {
  lin_30_0: pkts(encodeLinearAngular(30, 0, 120)),
  lin_n20_0: pkts(encodeLinearAngular(-20, 0, 120)),
  lin_0_120: pkts(encodeLinearAngular(0, 120, 120)),
  lin_0_n180: pkts(encodeLinearAngular(0, -180, 120)),
  lin_25_45: pkts(encodeLinearAngular(25, 45, 120)),
  stop: pkts(encodeLinearAngular(0, 0, 320)),
  fwd_30_35: pkts(encodeForward(30, 35)),
  'fwd_n25_35': pkts(encodeForward(-25, 35)),
  turn_90_90: pkts(encodeTurn(90, 90)),
  'turn_n45_90': pkts(encodeTurn(-45, 90)),
  rgb_1_0_0: pkts(encodeRGBChest(1, 0, 0)),
  'rgb_0_0.3_1': pkts(encodeRGBChest(0, 0.3, 1)),
  head_pan_60: pkts(encodeHeadPan(60)),
  // ref usa head_tilt_json(-20). El JS encodeHeadTilt aplica -tilt internamente,
  // así que para obtener json=-20 pasamos api tilt = 20.
  'head_tilt_json_-20': pkts(encodeHeadTilt(20)),
  speaker_hi: packMessages(encodeSpeaker('SYSTVOICE0', 1.0)).map(hex),
  speaker_stop: packMessages(encodeSpeaker('STOPSOUND', 1.0)).map(hex),
};

// Ejecuta el script Python de referencia.
const pyOut = execFileSync('python', [join(__dirname, 'ref_python.py')], { encoding: 'utf-8' });
const ref = JSON.parse(pyOut);

let ok = 0, fail = 0;
const keys = new Set([...Object.keys(js), ...Object.keys(ref)]);
for (const k of keys) {
  const a = JSON.stringify(js[k]);
  const b = JSON.stringify(ref[k]);
  if (a === b) { ok++; console.log(`  OK   ${k.padEnd(20)} ${a}`); }
  else { fail++; console.log(`  FAIL ${k.padEnd(20)} js=${a}  py=${b}`); }
}
console.log(`\n${ok} OK, ${fail} FAIL`);
process.exit(fail === 0 ? 0 : 1);
