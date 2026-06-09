from flask import Flask, request
import mysql.connector
from datetime import datetime
import requests
import threading
import time
import json
import paho.mqtt.client as mqtt

app = Flask(__name__)

# =========================
# MYSQL — Railway
# =========================
def get_db():
    return mysql.connector.connect(
        host="yamabiko.proxy.rlwy.net",
        user="root",
        password="KkBiCjCqXcxGbvOrxkbXNncDnsOwBamu",
        database="railway",
        port=40356,
        connection_timeout=10
    )

db     = get_db()
cursor = db.cursor()

def reconectar():
    global db, cursor
    try:
        db     = get_db()
        cursor = db.cursor()
    except Exception as e:
        print("❌ Reconexión fallida:", e)

# =========================
# TELEGRAM
# =========================
TELEGRAM_TOKEN = "8574260278:AAHqCdU2v31d7VjKUcJxT909FZI-3q_OAuY"
TELEGRAM_CHAT  = "8420783965"

def telegram_send(chat_id, mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": str(chat_id),
            "text": mensaje,
            "parse_mode": "Markdown"
        }, timeout=5)
    except Exception as e:
        print("⚠ Telegram:", e)

# =========================
# ALERTAS
# =========================
ultimo_vel = {}
cooldown   = {}

def enviar_alerta(tipo, mensaje, placa="X"):
    ahora = time.time()
    key   = f"{tipo}_{placa}"
    if key in cooldown and ahora - cooldown[key] < 10:
        return
    cooldown[key] = ahora
    telegram_send(TELEGRAM_CHAT, mensaje)
    print("🚨", tipo)

# =========================
# CONSTANTES SPARK GT 1.2
# =========================
Vd           = 1.2
Vd_m3        = Vd / 1000
R_AIRE       = 287.05
AFR          = 14.7
RHO_GASOLINA = 0.74
L_POR_GALON  = 3.78541

def calcular_ev(map_kpa, rpm, tps):
    ev_map  = map_kpa / 101.325
    rpm_n   = rpm / 3200.0
    ev_rpm  = max(0.60, min(1.0 - 0.15*(rpm_n-1.0)**2, 1.0))
    ev_tps  = 0.70 + (tps/100.0)*0.18
    return max(0.50, min(ev_map*0.55 + ev_rpm*0.25 + ev_tps*0.20, 0.92))

def calcular_consumo(rpm, map_kpa, iat_c, tps, engine_load, segundos):
    try:
        rpm_f   = max(float(rpm), 1.0)
        map_kpa = max(float(map_kpa), 10.0)
        T_K     = float(iat_c) + 273.15
        tps_f   = float(tps)
        load_f  = float(engine_load)
        Ev      = calcular_ev(map_kpa, rpm_f, tps_f)
        maf     = (map_kpa*1000 * Ev * Vd_m3 * (rpm_f/60)) / (2*R_AIRE*T_K)
        fuel_l  = (maf/AFR/RHO_GASOLINA) * (0.30 + load_f/100*0.70) * segundos
        return round(max(fuel_l/L_POR_GALON, 0), 6)
    except:
        return 0.0

def valor_valido(v, mn=-39, mx=9999):
    try: return mn <= float(v) <= mx
    except: return False

# =========================
# COMANDOS TELEGRAM
# =========================
def procesar_comando(texto, chat_id):
    texto = texto.strip().lower()
    placa = "JEP488" if "jep488" in texto else \
            "HWY839" if "hwy839" in texto else None
    try:
        if placa:
            cursor.execute("""
                SELECT placa,km_total,consumo_acum,rpm,
                       velocidad,temperatura,tps,map_pressure,engine_load
                FROM telemetria WHERE placa=%s
                ORDER BY id DESC LIMIT 1
            """, (placa,))
        else:
            cursor.execute("""
                SELECT placa,km_total,consumo_acum,rpm,
                       velocidad,temperatura,tps,map_pressure,engine_load
                FROM telemetria ORDER BY id DESC LIMIT 1
            """)
        row = cursor.fetchone()
        if not row:
            telegram_send(chat_id, "⚠️ Sin datos aún."); return

        p,km,cg,rv,vv,tv,tv2,mv,lv = row
        km=float(km); cg=float(cg)
        km_gal = km/cg if cg>0.0001 else 0
        g100   = cg/km*100 if km>0.1 else 0

        if '/consumo' in texto:
            msg = (f"🚗 *{p}*\n━━━━━━━━━━━━━━━━━━\n"
                   f"📍 Km: *{km:.2f}*\n"
                   f"⛽ Consumo: *{cg:.4f} gal*\n"
                   f"📊 {g100:.3f} gal/100km\n"
                   f"🏁 *{km_gal:.1f} km/gal*\n"
                   f"_Spark GT oficial: ~11.5 km/gal_")
        elif '/estado' in texto:
            msg = (f"🚗 *{p}*\n━━━━━━━━━━━━━━━━━━\n"
                   f"🔴 RPM: *{float(rv):.0f}*\n"
                   f"💨 Vel: *{float(vv):.0f} km/h*\n"
                   f"🌡️ Temp: *{float(tv):.1f}°C*\n"
                   f"🦶 TPS: *{float(tv2):.1f}%*\n"
                   f"📊 MAP: *{float(mv):.0f} kPa*\n"
                   f"⚙️ Load: *{float(lv):.1f}%*")
        elif '/km' in texto:
            msg = (f"🚗 *{p}*\n━━━━━━━━━━━━━━━━━━\n"
                   f"📍 Km: *{km:.3f}*\n"
                   f"⛽ *{cg:.4f} gal*\n"
                   f"🏁 *{km_gal:.1f} km/gal*")
        elif '/reset' in texto:
            cursor.execute("""INSERT INTO telemetria
                (placa,rpm,velocidad,temperatura,tps,map_pressure,
                 engine_load,iat,accX,accY,accZ,angleX,angleY,
                 km_total,consumo_gal,consumo_acum)
                VALUES(%s,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
            """, (p or "JEP488",))
            db.commit()
            msg = "🔄 *Reseteado a 0* ✔"
        else:
            msg = ("🤖 *BOT ARES OBD2*\n━━━━━━━━━━━━━━━━━━\n"
                   "/estado jep488\n/consumo jep488\n/km jep488\n/reset jep488")
        telegram_send(chat_id, msg)
    except Exception as e:
        reconectar()
        print("❌ Comando:", e)

# =========================
# POLLING TELEGRAM
# =========================
def polling_telegram():
    uid = 0
    print("🤖 Bot Ares iniciado...")
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={"offset": uid+1, "timeout": 10}, timeout=15)
            for u in r.json().get("result", []):
                uid = u["update_id"]
                msg = u.get("message", {})
                txt = msg.get("text", "")
                cid = msg.get("chat", {}).get("id", TELEGRAM_CHAT)
                if txt:
                    print("📩", txt)
                    procesar_comando(txt, cid)
        except Exception as e:
            print("⚠ Polling:", e)
        time.sleep(2)

# =========================
# GESTIÓN INGESTA MQTT
# =========================
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT   = 1883
MQTT_TOPIC  = "sparkgt/jep488/telemetria"

def on_connect(client, userdata, flags, rc):
    print(f"✔ Conectado exitosamente al Broker MQTT. Código: {rc}")
    client.subscribe(MQTT_TOPIC)

def on_message(client, userdata, msg):
    try:
        datos = json.loads(msg.payload.decode('utf-8'))
        
        placa       = datos.get("placa", "JEP488")
        rpm         = datos.get("rpm", 0)
        velocidad   = datos.get("velocidad", 0)
        temperatura = datos.get("temperatura", 0)
        tps         = datos.get("tps", 0)
        map_p       = datos.get("map", 0)
        engine_load = datos.get("load", 0)
        iat         = datos.get("iat", 25)
        accX        = datos.get("accx", 0)
        accY        = datos.get("accy", 0)
        accZ        = datos.get("accz", 0)
        angleX      = datos.get("anglex", 0)
        angleY      = datos.get("angley", 0)

        if not valor_valido(rpm, 1, 8000) or not valor_valido(temperatura, -39, 130):
            return

        vel  = float(velocidad)
        rpmf = float(rpm)
        tmpf = float(temperatura)
        tpsf = float(tps)
        ayf  = float(accY)

        global ultimo_vel
        if placa not in ultimo_vel: ultimo_vel[placa] = 0
        caida = ultimo_vel[placa] - vel

        # Monitoreo de alertas
        if vel > 120:
            enviar_alerta("VELOCIDAD", f"🚗 *{placa}*\n🏎️ Exceso velocidad *{vel:.0f} km/h*", placa)
        if ultimo_vel[placa] > 20 and caida > 25:
            enviar_alerta("FRENADA", f"🚗 *{placa}*\n⚠️ Frenada brusca\n{ultimo_vel[placa]:.0f}→{vel:.0f} km/h", placa)
        if rpmf > 3500 and tpsf > 35 and vel > 15:
            enviar_alerta("ACELERACION", f"🚗 *{placa}*\n🔥 Aceleración agresiva\nRPM: *{rpmf:.0f}*", placa)
        if abs(ayf) > 0.40 and vel > 25:
            enviar_alerta("CURVA", f"🚗 *{placa}*\n📐 Curva brusca *{ayf:.2f}g*", placa)
        if tmpf >= 109:
            enviar_alerta("TEMPERATURA", f"🚗 *{placa}*\n🌡️ Temp crítica *{tmpf:.1f}°C*", placa)

        ultimo_vel[placa] = vel

        # Integración con base de datos e histórico
        try:
            cursor.execute("""
                SELECT km_total,consumo_acum,fecha 
                FROM telemetria WHERE placa=%s 
                ORDER BY id DESC LIMIT 1
            """, (placa,))
            row = cursor.fetchone()
            if row:
                km_a = float(row[0]); ca = float(row[1])
                seg  = max(0.5, min((datetime.now()-row[2]).total_seconds(), 10))
            else:
                km_a = 0; ca = 0; seg = 2
            km_n  = km_a + (vel * seg/3600)
            cc    = calcular_consumo(rpm, map_p, iat, tps, engine_load, seg)
            cn    = ca + cc
        except:
            reconectar()
            km_n = cc = cn = 0

        try:
            cursor.execute("""
                INSERT INTO telemetria (
                    placa,rpm,velocidad,temperatura,tps,
                    map_pressure,engine_load,iat,
                    accX,accY,accZ,angleX,angleY,
                    km_total,consumo_gal,consumo_acum
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (placa,rpm,velocidad,temperatura,tps,map_p,engine_load,iat,
                  accX,accY,accZ,angleX,angleY,km_n,cc,cn))
            db.commit()
            print(f"✔ Inserción Completa - {placa} RPM:{rpm} Vel:{velocidad} Km:{km_n:.3f}")
        except Exception as e:
            print("❌ Error insertando datos en MySQL:", e)
            reconectar()

    except Exception as e:
        print("❌ Error procesando estructura JSON:", e)

# Inicialización del demonio MQTT
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

# =========================
# RUTAS HTTP (Flask / Render Health Check)
# =========================
@app.route('/')
def index():
    return "OBD Server OK (MQTT Listening)", 200

@app.route('/reset_km')
def reset_km():
    placa = request.args.get("placa","JEP488")
    try:
        cursor.execute("""INSERT INTO telemetria
            (placa,rpm,velocidad,temperatura,tps,map_pressure,
             engine_load,iat,accX,accY,accZ,angleX,angleY,
             km_total,consumo_gal,consumo_acum)
            VALUES(%s,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
        """, (placa,))
        db.commit()
        return "Reseteado", 200
    except Exception as e:
        return f"Error: {e}", 500

# =========================
# MAIN
# =========================
if __name__ == '__main__':
    # Hilos secundarios de procesamiento continuo
    threading.Thread(target=polling_telegram, daemon=True).start()
    mqtt_client.loop_start() 
    
    # Servidor web principal para evitar el timeout de Render
    app.run(host='0.0.0.0', port=5000, debug=False)