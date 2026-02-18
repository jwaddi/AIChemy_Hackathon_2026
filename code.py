import time
import board
import digitalio
import analogio
import adafruit_dht
import os
import wifi
import socketpool
import adafruit_minimqtt.adafruit_minimqtt as MQTT
import json

# Load env vars
try:
    WIFI_SSID = os.getenv("WIFI_SSID")
    WIFI_PASS = os.getenv("WIFI_PASSWORD")
    BROKER = os.getenv("BROKER")
    PORT = int(os.getenv("PORT"))
    DEVICE_ID = os.getenv("INATORNAME")
    TOPIC = os.getenv("INATORTOPIC")
    INTERVAL = int(os.getenv("ACQUIRETIME"))
except Exception as e:
    print(f"Config error: {e}")
    # Hard loop if config is missing
    while True: pass

# GPIO / Hardware Setup
led_red = digitalio.DigitalInOut(board.GP16)
led_yellow = digitalio.DigitalInOut(board.GP17)
led_green = digitalio.DigitalInOut(board.GP18)

for led in [led_red, led_yellow, led_green]:
    led.direction = digitalio.Direction.OUTPUT

# Button is active low (internal pull-up)
button = digitalio.DigitalInOut(board.GP15)
button.direction = digitalio.Direction.INPUT
button.pull = digitalio.Pull.UP 

# Sensors
adc_lm45 = analogio.AnalogIn(board.GP26)
dht_device = None
try:
    dht_device = adafruit_dht.DHT11(board.GP28)
except:
    pass # DHT init often fails on first boot, handled in loop

# Network Connection
print(f"Connecting to {WIFI_SSID}...")
try:
    wifi.radio.connect(WIFI_SSID, WIFI_PASS)
    print(f"IP: {wifi.radio.ipv4_address}")
    
    pool = socketpool.SocketPool(wifi.radio)
    
    # Using unencrypted TCP for port 1883
    mqtt_client = MQTT.MQTT(
        broker=BROKER,
        port=PORT,
        socket_pool=pool,
        is_ssl=False
    )
    
    mqtt_client.connect()
    print("MQTT Connected")

except Exception as e:
    print(f"Network error: {e}")
    # Visual cue for network failure
    while True:
        led_red.value = not led_red.value
        time.sleep(0.2)

def get_sensors():
    # LM45: 10mV per degree C, but we send raw voltage for FAIR compliance
    raw = adc_lm45.value
    volts = (raw * 3.3) / 65535
    
    # Internal temp approx for onboard validation logic
    t_approx = volts * 100 
    
    t_dht = None
    h_dht = None
    
    # DHT reads are timing-sensitive and can throw errors
    if dht_device:
        try:
            t_dht = dht_device.temperature
            h_dht = dht_device.humidity
        except RuntimeError:
            pass 
            
    return volts, t_approx, t_dht, h_dht

def update_status(t_approx, t_ref, trigger_active):
    # Manual intervention takes priority
    if trigger_active:
        led_yellow.value = True
        led_red.value = False
        led_green.value = False
        return "EVENT_TAGGED"
    
    led_yellow.value = False
    
    # Check sensor divergence
    # If delta > 3.0C, flag system as unstable
    if t_ref is not None:
        delta = abs(t_approx - t_ref)
        if delta > 3.0:
            led_red.value = True
            led_green.value = False
            return "SYSTEM_UNSTABLE"
            
        led_red.value = False
        led_green.value = True
        return "SYSTEM_STABLE"
    
    # Default error state if reference sensor is offline
    led_red.value = True
    led_green.value = False
    return "SYSTEM_UNSTABLE"

# Main Loop
while True:
    try:
        volts, t_approx, t_ref, h_ref = get_sensors()
        btn_state = not button.value # Invert for active low
        
        status_msg = update_status(t_approx, t_ref, btn_state)
        
        payload = {
            "id": DEVICE_ID,
            "status": status_msg,
            "metrics": {
                "analogue_approx": round(t_approx, 2),
                "digital_ref": t_ref,
                "delta": round(abs(t_approx - t_ref), 2) if t_ref else None
            },
            "raw": {
                "v": round(volts, 4),
                "h": h_ref
            }
        }
        
        print(f"[{status_msg}] Publishing...")
        mqtt_client.publish(TOPIC, json.dumps(payload))
        
    except Exception as e:
        print(f"Error in loop: {e}")
        try:
            mqtt_client.reconnect()
        except:
            pass
            
    time.sleep(INTERVAL)
