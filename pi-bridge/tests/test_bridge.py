"""Black-box tests of the compiled C++ service; no serial/GPIO access."""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'pc-runtime/src'))
from tabletop_os.frame_protocol import encode_frame
from tabletop_os.config import NetworkConfig
from tabletop_os.device import NetworkDevice

BRIDGE = None


def available_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = json.loads((ROOT/'pi-bridge/config/tabletop-os.json').read_text())
        n = self.config['network']
        n.update(bind_address='127.0.0.1', control_port=available_port(socket.SOCK_STREAM),
                 cursor_port=available_port(socket.SOCK_DGRAM), frame_port=available_port(socket.SOCK_DGRAM),
                 heartbeat_timeout_ms=300)
        self.config['serial']['enabled'] = False
        self.config['display']['enabled'] = False
        path = self.directory/'config.json'
        path.write_text(json.dumps(self.config))
        self.log = (self.directory/'bridge.log').open('w+')
        options = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {}
        self.process = subprocess.Popen([str(BRIDGE),'--config',str(path)], stdout=self.log, stderr=self.log, **options)
        until = time.monotonic()+5
        while True:
            try:
                self.client = socket.create_connection(('127.0.0.1',n['control_port']), timeout=.3)
                self.client.settimeout(1)
                break
            except OSError:
                if self.process.poll() is not None or time.monotonic()>until:
                    self.log.seek(0)
                    self.fail('Bridge did not start: '+self.log.read())
                time.sleep(.03)
        self.buffer = bytearray()

    def tearDown(self):
        self.client.close()
        if self.process.poll() is None:
            try:
                self.process.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
                self.process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait(timeout=2)
        self.log.close()
        self.temp.cleanup()

    def send(self, kind, request='test', **fields):
        self.client.sendall((json.dumps({'v':1,'type':kind,'request_id':request,**fields})+'\n').encode())

    def read(self, wanted=None):
        until=time.monotonic()+2
        while time.monotonic()<until:
            while b'\n' not in self.buffer:
                data=self.client.recv(8192)
                if not data:
                    raise RuntimeError('EOF')
                self.buffer.extend(data)
            line,_,remaining=self.buffer.partition(b'\n')
            self.buffer=bytearray(remaining)
            message=json.loads(line)
            if wanted is None or message['type']==wanted:
                return message
        self.fail(f'Missing {wanted}')

    def heartbeat(self):
        self.send('heartbeat')
        self.assertEqual(self.read('heartbeat.ack')['reply_to'],'test')

    def test_stream_fragmentation_multiple_lines_and_errors(self):
        self.client.sendall(b'{"v":1,"type":"status.')
        time.sleep(.02)
        self.client.sendall(b'get","request_id":"fragment"}\n{"v":1,"type":"ping","request_id":"second"}\n')
        self.assertEqual(self.read('status')['reply_to'],'fragment')
        self.assertEqual(self.read('pong')['reply_to'],'second')
        self.client.sendall(b'{broken}\n')
        self.assertEqual(self.read('error')['code'],'INVALID_JSON')
        self.send('motion.move_to',x_mm=20,y_mm=20,speed_mm_s=2)
        self.assertEqual(self.read('error')['code'],'MOTION_DISABLED')

    def test_only_heartbeat_renews_lease(self):
        self.heartbeat()
        self.send('status.get')
        self.assertTrue(self.read('status')['control_lease_valid'])
        time.sleep(.4)
        self.read('control.lease_expired')
        self.send('ping')
        self.assertFalse(self.read('pong')['control_lease_valid'])

    def test_second_control_client_is_rejected(self):
        with socket.create_connection(('127.0.0.1',self.config['network']['control_port']),timeout=1) as other:
            self.assertEqual(json.loads(other.recv(4096))['code'],'CONTROL_CLIENT_BUSY')

    def test_oversized_control_line_is_rejected_and_closed(self):
        self.client.sendall(b'a'*4097)
        self.assertEqual(self.read('error')['code'],'CONTROL_LINE_TOO_LARGE')
        self.assertEqual(self.client.recv(1),b'')

    def test_python_frames_decode_in_cpp_and_clear_on_expired_lease(self):
        self.heartbeat()
        self.send('display.open',session=123)
        self.assertEqual(self.read('display.opened')['session'],123)
        packets=encode_frame(bytes(i%251 for i in range(12288)),123,77)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            for packet in reversed(packets[:-1]):
                udp.sendto(packet,('127.0.0.1',self.config['network']['frame_port']))
            time.sleep(.015)
            self.send('status.get')
            self.assertEqual(self.read('status')['display']['frame_seq'],0)
            udp.sendto(packets[-1],('127.0.0.1',self.config['network']['frame_port']))
        time.sleep(.015)
        self.send('status.get')
        self.assertEqual(self.read('status')['display']['frame_seq'],77)
        time.sleep(.4)
        self.read('control.lease_expired')
        self.send('status.get')
        self.assertEqual(self.read('status')['display']['session'],0)

    def test_real_pc_network_worker_keeps_lease_and_publishes(self):
        self.client.close()
        time.sleep(.05)
        n=self.config['network']
        # Service test timeout is 300 ms; worker uses 100 ms heartbeats.
        device=NetworkDevice(NetworkConfig('127.0.0.1',n['control_port'],n['frame_port'],100,100,700))
        device.start()
        try:
            until=time.monotonic()+2
            while not device.snapshot().received_at and time.monotonic()<until:
                time.sleep(.02)
            self.assertGreater(device.snapshot().received_at,0)
            device.publish(bytes(12288))
            time.sleep(.5)
            self.assertTrue(device.snapshot().lease_valid)
            self.assertGreater(device.display_status().get('frame_seq',0),0)
            self.assertFalse(any(e.get('type')=='control.lease_expired' for e in device.poll()))
        finally:
            device.close()

    def test_pc_stalled_main_loop_does_not_keep_motion_lease_alive(self):
        self.client.close()
        time.sleep(.05)
        n=self.config['network']
        device=NetworkDevice(NetworkConfig('127.0.0.1',n['control_port'],n['frame_port'],100,100,400))
        device.start()
        try:
            until=time.monotonic()+1
            while not device.snapshot().received_at and time.monotonic()<until:
                time.sleep(.01)
            self.assertGreater(device.snapshot().received_at,0)
            time.sleep(.65)  # no Runtime.publish; worker must close the session
            self.assertEqual(device.snapshot().received_at,0)
            self.assertTrue(any(e.get('type')=='connection.lost' for e in device.poll()))
        finally:
            device.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--bridge',type=Path,required=True)
    args,remaining=parser.parse_known_args()
    BRIDGE=args.bridge.resolve()
    unittest.main(argv=[sys.argv[0],*remaining])
