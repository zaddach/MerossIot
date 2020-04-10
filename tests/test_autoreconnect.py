import os
import time
import unittest
from threading import Event, Thread
import proxy
import socks

from meross_iot.cloud.client_status import ClientStatus
from meross_iot.cloud.devices.power_plugs import GenericPlug
from meross_iot.cloud.exceptions.CommandTimeoutException import CommandTimeoutException
from meross_iot.manager import MerossManager

EMAIL = os.environ.get('MEROSS_EMAIL')
PASSWORD = os.environ.get('MEROSS_PASSWORD')
PROXY_PORT = 5001


class TestAutoreconnect(unittest.TestCase):
    def setUp(self):
        self.manager = MerossManager(meross_email=EMAIL, meross_password=PASSWORD, auto_reconnect=True)

    class WorkerThread(object):
        def __init__(self, device: GenericPlug):
            self.device = device
            self._t = Thread(target=self._run)
            self.stopped = Event()

        def start(self):
            self._t.start()

        def stop(self):
            self.stopped.set()
            self._t.join()

        def _run(self):
            while True:
                try:
                    status = self.device.get_channel_status(0)
                    if status:
                        self.device.turn_off()
                    else:
                        self.device.turn_on()
                except CommandTimeoutException:
                    print("Command timed out.")
                    pass
                finally:
                    if self.stopped.wait(1):
                        break

    def test_single_threaded_connection_drop(self):
        # Allocate the proxy
        dev = None
        print("Connecting through proxy...")
        with proxy.start(['--num-workers', '1', '--hostname', '127.0.0.1', '--port', str(PROXY_PORT)]):
            # Configure the manager client to use the proxy
            self.manager._cloud_client._mqtt_client.proxy_set(proxy_type=socks.HTTP, proxy_addr="127.0.0.1",
                                                              proxy_port=PROXY_PORT)
            # Connect
            self.manager.start()
            self.assertTrue(self.manager._cloud_client.connection_status.check_status(ClientStatus.SUBSCRIBED))

            # Wait a bit before closing the proxy. In the meanwhile, select a device to be used for testing.
            devices = self.manager.get_supported_devices()
            if len(devices) < 1:
                self.skipTest("Could not find any device to test...")
            dev = devices[0]
            status = dev.get_status(force_status_refresh=True)
            print("Device status: %s" % str(status))
            time.sleep(5)
            print("Closing the proxy to trigger disconnection")

        print("Proxy closed")
        self.assertTrue(self.manager._cloud_client.connection_status.check_status(ClientStatus.CONNECTION_DROPPED))

        try:
            new_status = dev.get_status(force_status_refresh=True)
            raise Exception("Device was still able to reconnect.")
        except CommandTimeoutException:
            print("Device is unreachable. That's ok!")

        print("Reconnecting the proxy...")
        with proxy.start(['--num-workers', '1', '--hostname', '127.0.0.1', '--port', str(PROXY_PORT)]):
            time.sleep(5)
            new_status = dev.get_status(force_status_refresh=True)
            print("New device status: %s" % new_status)

            self.manager.stop()

    def test_multithreaded_connection_drop(self):
        # Allocate the proxy
        workers = []
        print("Connecting through proxy...")
        with proxy.start(['--num-workers', '1', '--hostname', '127.0.0.1', '--port', str(PROXY_PORT)]):
            # Configure the manager client to use the proxy
            self.manager._cloud_client._mqtt_client.proxy_set(proxy_type=socks.HTTP, proxy_addr="127.0.0.1",
                                                              proxy_port=PROXY_PORT)
            # Connect
            self.manager.start()

            # Start a worker for every plug
            for p in self.manager.get_devices_by_kind(GenericPlug):
                w = TestAutoreconnect.WorkerThread(p)
                workers.append(w)
                w.start()

            print("Started workers. Waiting a bit....")
            time.sleep(10)

            print("Dropping connection...")

        print("Proxy has been closed. Waiting 120 seconds to trigger timeouts")
        time.sleep(120)

        print("Establishing connection back again...")
        with proxy.start(['--num-workers', '1', '--hostname', '127.0.0.1', '--port', str(PROXY_PORT)]):
            print("Proxy online again. Waiting a bit...")
            time.sleep(10)
            print("Stopping workers")
            for w in workers:
                w.stop()

            print("Closing manager.")
            self.manager.stop()

    def tearDown(self):
        pass
