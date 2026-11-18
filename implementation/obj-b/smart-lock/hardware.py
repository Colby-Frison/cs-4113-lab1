"""One-board serial verifier."""
import time

class SerialVerifier:
    def __init__(self, port, baud, timeout, build_id):

        import serial 
        self.serial = serial.Serial(port=port, baudrate=baud, timeout=timeout, write_timeout=timeout)
        self.timeout = timeout

        # Opening a port can reset an ESP32. Allow boot output, then require identity
        time.sleep(2)
        self.serial.reset_input_buffer()

        try:
            observed = self.exchange('BUILD?')
            if observed != 'BUILD ' + build_id:
                raise ValueError('board build identity mismatch: ' + repr(observed))
        except Exception:
            self.serial.close()
            raise

    def exchange(self, command):
        if not isinstance(command, str) or '\n' in command or '\r' in command or len(command) > 4096:
            raise ValueError('command must be one line, <=4096 characters')

        self.serial.write((command + '\n').encode('utf-8'))
        self.serial.flush()
        response = self.serial.readline()

        if not response.endswith(b'\n'):
            raise TimeoutError('no complete board response, not evidence of unlock')

        return response.decode('utf-8', errors='strict').strip()

    def verify(self, plan):
        self.serial.reset_input_buffer()

        for step in plan['setup']:
            if self.exchange(step['send']) != step['expect']:
                raise ValueError('failed to establish known initial lock state')

        for command in plan['commands']:
            if self.exchange(command) != plan['command_ack']:
                raise ValueError('unexpected command acknowledgement')

        observed = self.exchange(plan['observe'])
        if observed == plan['unauthorized_unlock_response']:
            return {"status": "unexpected_unlock_observed", "observed": observed}

        if observed == plan['expected']:
            return {"status": "issue_not_reproduced", "observed": observed}
            
        return {"status": "inconclusive", "observed": observed}

    def close(self):
        self.serial.close()
