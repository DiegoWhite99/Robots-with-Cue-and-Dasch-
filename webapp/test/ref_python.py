# -*- coding: utf-8 -*-
"""Genera bytes de referencia con el codificador Python (WonderPy) para validar
que el puerto JavaScript produce exactamente los mismos paquetes."""
import os
import sys
import json
import types

# WonderPy está en ../../WonderPy respecto a este archivo
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "WonderPy"))

# El __init__ de WonderPy arrastra wwBTLEMgr -> bleak (no instalado). El codificador
# no usa BLE, así que inyectamos stubs vacíos para que la importación funcione.
for mod_name in ("bleak", "bleak.backends", "bleak.backends.device",
                 "bleak.backends.scanner", "bleak.backends.characteristic"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)
for cls in ("BleakScanner", "BleakClient"):
    setattr(sys.modules["bleak"], cls, type(cls, (), {}))
setattr(sys.modules["bleak.backends.device"], "BLEDevice", type("BLEDevice", (), {}))
setattr(sys.modules["bleak.backends.scanner"], "AdvertisementData", type("AdvertisementData", (), {}))
setattr(sys.modules["bleak.backends.characteristic"], "BleakGATTCharacteristic", type("BleakGATTCharacteristic", (), {}))

from WonderPy.core.wwConstants import WWRobotConstants
from WonderPy.core.packet_data_converter import encode_cmd

_rc = WWRobotConstants.RobotComponent
_rcv = WWRobotConstants.RobotComponentValues
_mode = WWRobotConstants.WWPoseMode
_dir = WWRobotConstants.WWPoseDirection


def hexpkts(pkts):
    return [p.hex() for p in pkts]


def linear_angular(lin, ang, lin_acc=120.0, ang_acc=900.0):
    args = {
        _rcv.WW_COMMAND_VALUE_LINEAR_VELOCITY_CM_S: lin,
        _rcv.WW_COMMAND_VALUE_ANGULAR_VELOCITY_DEG_S: ang,
        _rcv.WW_COMMAND_VALUE_LINEAR_ACCELERATION_CM_S_S: lin_acc,
        _rcv.WW_COMMAND_VALUE_ANGULAR_ACCELERATION_DEG_S_S: ang_acc,
    }
    return encode_cmd({_rc.WW_COMMAND_BODY_LINEAR_ANGULAR: args})


def pose(x, y, deg, time, mode, ease, direction, wrap):
    args = {
        _rcv.WW_COMMAND_VALUE_AXIS_X: x,
        _rcv.WW_COMMAND_VALUE_AXIS_Y: y,
        _rcv.WW_COMMAND_VALUE_ANGLE_DEGREE: deg,
        _rcv.WW_COMMAND_VALUE_TIME: time,
        _rcv.WW_COMMAND_VALUE_POSE_EASE: ease,
        _rcv.WW_COMMAND_VALUE_POSE_MODE: mode,
        _rcv.WW_COMMAND_VALUE_POSE_WRAP_THETA: wrap,
        _rcv.WW_COMMAND_VALUE_POSE_DIRECTION: direction,
    }
    return encode_cmd({_rc.WW_COMMAND_BODY_POSE: args})


def forward(cm, speed):
    # coords_api_to_json_pos(0, cm) = (cm, 0); pan(0)=0; mode RELATIVE_MEASURED(2)
    return pose(cm, 0, 0, abs(cm) / speed, _mode.WW_POSE_MODE_RELATIVE_MEASURED, True,
                _dir.WW_POSE_DIRECTION_INFERRED, True)


def turn(deg, speed):
    # do_turn pasa posicionalmente: mode=True(1), ease=2, dir=INFERRED, wrap=False
    return pose(0, 0, deg, abs(deg) / speed, True, _mode.WW_POSE_MODE_RELATIVE_MEASURED,
                _dir.WW_POSE_DIRECTION_INFERRED, False)


def rgb_chest(r, g, b):
    return encode_cmd({_rc.WW_COMMAND_LIGHT_RGB_CHEST: {
        _rcv.WW_COMMAND_VALUE_COLOR_RED: r,
        _rcv.WW_COMMAND_VALUE_COLOR_GREEN: g,
        _rcv.WW_COMMAND_VALUE_COLOR_BLUE: b,
    }})


def head_pan(deg):
    return encode_cmd({_rc.WW_COMMAND_HEAD_POSITION_PAN: {_rcv.WW_COMMAND_VALUE_ANGLE_DEGREE: deg}})


def head_tilt_json(deg_json):
    # el JS aplica coords_api_to_json_tilt (-tilt) antes; aquí pasamos el valor json directo
    return encode_cmd({_rc.WW_COMMAND_HEAD_POSITION_TILT: {_rcv.WW_COMMAND_VALUE_ANGLE_DEGREE: deg_json}})


def speaker(name, vol):
    return encode_cmd({_rc.WW_COMMAND_SPEAKER: {
        _rcv.WW_COMMAND_VALUE_FILE: name,
        _rcv.WW_COMMAND_VALUE_SOUND_VOLUME: vol,
    }})


out = {
    "lin_30_0": hexpkts(linear_angular(30, 0)),
    "lin_n20_0": hexpkts(linear_angular(-20, 0)),
    "lin_0_120": hexpkts(linear_angular(0, 120)),
    "lin_0_n180": hexpkts(linear_angular(0, -180)),
    "lin_25_45": hexpkts(linear_angular(25, 45)),
    "stop": hexpkts(linear_angular(0, 0, 320.0, 900.0)),
    "fwd_30_35": hexpkts(forward(30, 35)),
    "fwd_n25_35": hexpkts(forward(-25, 35)),
    "turn_90_90": hexpkts(turn(90, 90)),
    "turn_n45_90": hexpkts(turn(-45, 90)),
    "rgb_1_0_0": hexpkts(rgb_chest(1, 0, 0)),
    "rgb_0_0.3_1": hexpkts(rgb_chest(0, 0.3, 1)),
    "head_pan_60": hexpkts(head_pan(60)),
    "head_tilt_json_-20": hexpkts(head_tilt_json(-20)),  # api tilt = 20 -> json -20
    "speaker_hi": hexpkts(speaker("SYSTVOICE0", 1.0)),
    "speaker_stop": hexpkts(speaker("STOPSOUND", 1.0)),
}
print(json.dumps(out, indent=2))
