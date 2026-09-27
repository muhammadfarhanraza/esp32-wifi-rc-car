import network
import socket
from machine import Pin, PWM
import time

# ================== SERVO SETUP (initialize FIRST) ==================
# Initializing the servo before the motor PWMs helps avoid ESP32's
# PWM timer-sharing issue, where paired channels are forced to the
# same frequency (motors need ~1kHz, servo needs 50Hz).
servo = PWM(Pin(18), freq=50)

SERVO_CENTER = 90
SERVO_LEFT   = 45     # adjust to your linkage's real mechanical limit
SERVO_RIGHT  = 135    # adjust to your linkage's real mechanical limit

def set_servo_angle(angle):
    angle = max(0, min(180, angle))
    us = int(500 + (angle / 180) * 2000)      # 0.5ms - 2.5ms pulse
    duty = int((us / 20000) * 65535)          # 20ms period -> duty_u16
    servo.duty_u16(duty)

set_servo_angle(SERVO_CENTER)
print("Servo freq after init:", servo.freq())   # should print 50

# ================== MOTOR DRIVER SETUP (L298N) ==================
ENA = PWM(Pin(25), freq=1000)
IN1 = Pin(26, Pin.OUT)
IN2 = Pin(27, Pin.OUT)

IN3 = Pin(32, Pin.OUT)
IN4 = Pin(33, Pin.OUT)
ENB = PWM(Pin(13), freq=1000)

MAX_DUTY = 65535
DEFAULT_SPEED = 100          # percent, full speed by default
current_speed = DEFAULT_SPEED

def set_speed(percent):
    percent = max(0, min(100, percent))
    duty = int((percent / 100) * MAX_DUTY)
    ENA.duty_u16(duty)
    ENB.duty_u16(duty)

def motor_forward():
    IN1.value(1); IN2.value(0)
    IN3.value(1); IN4.value(0)
    set_speed(current_speed)

def motor_backward():
    IN1.value(0); IN2.value(1)
    IN3.value(0); IN4.value(1)
    set_speed(current_speed)

def motor_stop():
    IN1.value(0); IN2.value(0)
    IN3.value(0); IN4.value(0)
    ENA.duty_u16(0)
    ENB.duty_u16(0)

motor_stop()
print("Motor ENA freq:", ENA.freq())
print("Motor ENB freq:", ENB.freq())

# ================== WIFI ACCESS POINT ==================
AP_SSID = "RC_CAR"
AP_PASSWORD = "12345678"     # must be 8+ characters

ap = network.WLAN(network.AP_IF)
ap.active(True)
ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=network.AUTH_WPA_WPA2_PSK)

while not ap.active():
    time.sleep(0.1)

print("Access Point active")
print("SSID:", AP_SSID)
print("IP:", ap.ifconfig()[0])   # usually 192.168.4.1

# ================== WEB PAGE ==================
HTML = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 RC Car</title>
<style>
  body { font-family: Arial; text-align:center; background:#222; color:#fff; margin:0; padding:10px;}
  h2 { margin-top:10px; }
  .row { display:flex; justify-content:center; margin:10px 0; }
  button {
    width:90px; height:90px; margin:8px; font-size:20px;
    border-radius:12px; border:none; background:#444; color:#fff;
    touch-action:none; user-select:none;
  }
  button:active, button.active { background:#0a84ff; }
  #stopBtn { background:#c62828; width:200px; }
  input[type=range]{ width:80%; }
</style>
</head>
<body>
<h2>ESP32 WiFi RC Car</h2>

<div class="row">
  <button id="fwd">FORWARD</button>
</div>
<div class="row">
  <button id="left">LEFT</button>
  <button id="stopBtn">STOP</button>
  <button id="right">RIGHT</button>
</div>
<div class="row">
  <button id="bwd">BACKWARD</button>
</div>

<p>Speed: <span id="spdVal">100</span>%</p>
<input type="range" id="speed" min="0" max="100" value="100">

<script>
function send(cmd){
  fetch('/' + cmd).catch(()=>{});
}

function bindHold(id, downCmd, upCmd){
  const el = document.getElementById(id);
  const start = (e)=>{ e.preventDefault(); el.classList.add('active'); send(downCmd); };
  const end   = (e)=>{ e.preventDefault(); el.classList.remove('active'); send(upCmd); };
  el.addEventListener('touchstart', start);
  el.addEventListener('touchend', end);
  el.addEventListener('mousedown', start);
  el.addEventListener('mouseup', end);
  el.addEventListener('mouseleave', end);
}

bindHold('fwd', 'F', 'S');
bindHold('bwd', 'B', 'S');
bindHold('left', 'L', 'C');
bindHold('right', 'R', 'C');

document.getElementById('stopBtn').addEventListener('click', ()=> send('S'));

const speedSlider = document.getElementById('speed');
speedSlider.addEventListener('input', ()=>{
  document.getElementById('spdVal').innerText = speedSlider.value;
  fetch('/V' + speedSlider.value).catch(()=>{});
});
</script>
</body>
</html>
"""

# ================== HTTP SERVER ==================
addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(addr)
s.listen(1)
print("Web server listening on", addr)

def handle_request(path):
    global current_speed

    if path.startswith('/F'):
        motor_forward()
    elif path.startswith('/B'):
        motor_backward()
    elif path.startswith('/S'):
        motor_stop()
    elif path.startswith('/L'):
        set_servo_angle(SERVO_LEFT)
    elif path.startswith('/R'):
        set_servo_angle(SERVO_RIGHT)
    elif path.startswith('/C'):
        set_servo_angle(SERVO_CENTER)
    elif path.startswith('/V'):
        try:
            current_speed = int(path[2:])
        except ValueError:
            pass

while True:
    cl = None
    try:
        cl, cl_addr = s.accept()
        cl.settimeout(3.0)
        request = cl.recv(1024).decode()

        try:
            path = request.split(' ')[1]
        except IndexError:
            path = '/'

        if path == '/' or path.startswith('/index.html'):
            response = HTML
        else:
            handle_request(path)
            response = "OK"

        cl.send('HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n')
        cl.sendall(response.encode())
        cl.close()

    except OSError:
        if cl is not None:
            try:
                cl.close()
            except OSError:
                pass
        # Safety: stop motors on any connection error
        motor_stop()