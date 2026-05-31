from flask import Flask, request
import mysql.connector
from datetime import datetime
import requests
import threading
import time

app = Flask(__name__)

# =========================
# MYSQL
# =========================
db = mysql.connector.connect(
    host="localhost", user="root",
    password="1234", database="obd_car", port=3306
)
cursor = db.cursor()

# =========================
# TELEGRAM
# =========================
TELEGRAM_TOKEN = "8574260278:AAHqCdU2v31d7VjKUcJxT909FZI-3q_OAuY"
TELEGRAM_CHAT  = "8420783965"

# =========================
# VARIABLES
# =========================
ultimo_vel = {}
cooldown   = {}

# =========================
# CONSTANTES SPARK GT 1.2
# =========================
Vd           = 1.2
Vd_m3        = Vd / 1000
R_AIRE       = 287.05
AFR          = 14.7
RHO_GASOLINA = 0.74
L_POR_GALON  = 3.78541

# =========================
# TELEGRAM SEND
# =========================
def telegram_send(chat_id, mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": str(chat_id),
            "text": mensaje,
            "parse_mode": "Markdown"
        }, timeout=5)
        print("📨 Telegram enviado")
    except Exception as e:
        print("⚠ Error Telegram:", e)

# =========================
# ALERTAS — directo a Telegram
# =========================
def enviar_alerta(tipo, mensaje, datos={}):
    ahora = time.time()
    key   = f"{tipo}_{datos.get('placa','X')}"
    if key in cooldown and ahora - cooldown[key] < 10:
        return
    cooldown[key] = ahora
    telegram_send(TELEGRAM_CHAT, mensaje)
    print("🚨 ALERTA:", tipo)

# =========================
# EFICIENCIA VOLUMÉTRICA
# =========================
def calcular_ev(map_kpa, rpm, tps):
    ev_map  = map_kpa / 101.325
    rpm_norm = rpm / 3200.0
    ev_rpm   = 1.0 - 0.15 * (rpm_norm - 1.0) ** 2
    ev_rpm   = max(0.60, min(ev_rpm, 1.0))
    ev_tps   = 0.70 + (tps / 100.0) * 0.18
    ev = (ev_map * 0.55) + (ev_rpm * 0.25) + (ev_tps * 0.20)
    return max(0.50, min(ev, 0.92))

# =========================
# CONSUMO
# =========================
def calcular_consumo(rpm, map_kpa, iat_c, tps, engine_load, segundos):
    try:
        rpm_f    = max(float(rpm), 1.0)
        map_kpa  = max(float(map_kpa), 10.0)
        iat_c    = float(iat_c)
        tps_f    = float(tps)
        load_f   = float(engine_load)
        T_K      = iat_c + 273.15
        map_pa   = map_kpa * 1000.0
        n_rev_s  = rpm_f / 60.0
        Ev       = calcular_ev(map_kpa, rpm_f, tps_f)
        maf_kg_s = (map_pa * Ev * Vd_m3 * n_rev_s) / (2.0 * R_AIRE * T_K)
        factor_load   = 0.30 + (load_f / 100.0) * 0.70
        maf_corregido = maf_kg_s * factor_load
        fuel_kg_s     = maf_corregido / AFR
        fuel_l_s      = fuel_kg_s / RHO_GASOLINA
        fuel_l_total  = fuel_l_s * segundos
        fuel_gal      = fuel_l_total / L_POR_GALON
        return round(max(fuel_gal, 0.0), 6)
    except Exception as e:
        print("❌ Error consumo:", e)
        return 0.0

# =========================
# VALIDAR
# =========================
def valor_valido(v, minimo=-39, maximo=9999):
    try:
        return minimo <= float(v) <= maximo
    except:
        return False

# =========================
# COMANDOS TELEGRAM
# =========================
def procesar_comando(texto, chat_id):
    texto = texto.strip().lower()
    placa = None
    if "jep488" in texto: placa = "JEP488"
    elif "hwy839" in texto: placa = "HWY839"
    try:
        if placa:
            cursor.execute("""
                SELECT placa, km_total, consumo_acum,
                       rpm, velocidad, temperatura,
                       tps, map_pressure, engine_load
                FROM telemetria WHERE placa = %s
                ORDER BY id DESC LIMIT 1
            """, (placa,))
        else:
            cursor.execute("""
                SELECT placa, km_total, consumo_acum,
                       rpm, velocidad, temperatura,
                       tps, map_pressure, engine_load
                FROM telemetria ORDER BY id DESC LIMIT 1
            """)
        row = cursor.fetchone()
        if not row:
            telegram_send(chat_id, "⚠️ Sin datos.")
            return

        placa_db = row[0]
        km       = float(row[1])
        cons_gal = float(row[2])
        rpm_v    = float(row[3])
        vel_v    = float(row[4])
        temp_v   = float(row[5])
        tps_v    = float(row[6])
        map_v    = float(row[7])
        load_v   = float(row[8])

        km_gal = (km / cons_gal)       if cons_gal > 0.0001 else 0
        gal100 = (cons_gal / km * 100) if km > 0.1 else 0
        cons_l = cons_gal * L_POR_GALON

        if '/consumo' in texto:
            respuesta = (
                f"🚗 *{placa_db}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 Km: *{km:.2f} km*\n"
                f"⛽ Consumo: *{cons_gal:.4f} gal*\n"
                f"🛢 Litros: *{cons_l:.3f} L*\n"
                f"📊 gal/100km: *{gal100:.3f}*\n"
                f"🏁 km/gal: *{km_gal:.1f}*"
            )
        elif '/estado' in texto:
            ev_actual = calcular_ev(map_v, rpm_v, tps_v)
            respuesta = (
                f"🚗 *{placa_db}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔴 RPM: *{rpm_v:.0f}*\n"
                f"💨 Velocidad: *{vel_v:.0f} km/h*\n"
                f"🌡️ Temp: *{temp_v:.1f}°C*\n"
                f"🦶 TPS: *{tps_v:.1f}%*\n"
                f"📊 MAP: *{map_v:.0f} kPa*\n"
                f"⚙️ Load: *{load_v:.1f}%*\n"
                f"🔬 EV: *{ev_actual*100:.1f}%*"
            )
        elif '/km' in texto:
            respuesta = (
                f"🚗 *{placa_db}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📍 Km: *{km:.3f} km*\n"
                f"⛽ Consumo: *{cons_gal:.4f} gal*\n"
                f"🏁 Rendimiento: *{km_gal:.1f} km/gal*"
            )
        else:
            respuesta = (
                f"🤖 *BOT OBD2*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"/estado jep488\n"
                f"/consumo jep488\n"
                f"/km jep488"
            )
        telegram_send(chat_id, respuesta)
    except Exception as e:
        print("❌ Error comando:", e)
        telegram_send(chat_id, f"❌ Error: {str(e)}")

# =========================
# POLLING TELEGRAM
# =========================
def polling_telegram():
    ultimo_update_id = 0
    print("🤖 Bot iniciado...")
    while True:
        try:
            url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(url, params={
                "offset": ultimo_update_id + 1,
                "timeout": 10
            }, timeout=15)
            data = resp.json()
            for update in data.get("result", []):
                ultimo_update_id = update["update_id"]
                msg     = update.get("message", {})
                texto   = msg.get("text", "")
                chat_id = msg.get("chat", {}).get("id", TELEGRAM_CHAT)
                if texto:
                    print("📩", texto)
                    procesar_comando(texto, chat_id)
        except Exception as e:
            print("⚠ Polling:", e)
        time.sleep(2)

# =========================
# RUTA OBD
# =========================
@app.route('/obd')
def obd():
    placa       = request.args.get("placa",       "SIN_PLACA")
    rpm         = request.args.get("rpm",          0)
    velocidad   = request.args.get("velocidad",    0)
    temperatura = request.args.get("temperatura",  0)
    tps         = request.args.get("tps",          0)
    map_pressure= request.args.get("map",          0)
    engine_load = request.args.get("load",         0)
    iat         = request.args.get("iat",          25)
    accX        = request.args.get("accx",         0)
    accY        = request.args.get("accy",         0)
    accZ        = request.args.get("accz",         0)
    angleX      = request.args.get("anglex",       0)
    angleY      = request.args.get("angley",       0)

    if not valor_valido(rpm, 1, 8000):
        return "RPM inválida", 200
    if not valor_valido(temperatura, -39, 130):
        return "Temp inválida", 200

    vel   = float(velocidad)
    rpmf  = float(rpm)
    tempf = float(temperatura)
    tpsf  = float(tps)
    accYf = float(accY)

    if placa not in ultimo_vel:
        ultimo_vel[placa] = 0

    caida_vel = ultimo_vel[placa] - vel

    if vel > 120:
        enviar_alerta("VELOCIDAD",
            f"🚗 *{placa}*\n🏎️ Exceso velocidad *{vel:.0f} km/h*",
            {"placa": placa})

    if ultimo_vel[placa] > 20 and caida_vel > 25:
        enviar_alerta("FRENADA",
            f"🚗 *{placa}*\n⚠️ Frenada brusca\n"
            f"{ultimo_vel[placa]:.0f} → {vel:.0f} km/h",
            {"placa": placa})

    if rpmf > 3500 and tpsf > 35 and vel > 15:
        enviar_alerta("ACELERACION",
            f"🚗 *{placa}*\n🔥 Aceleración agresiva\n"
            f"RPM: *{rpmf:.0f}* | TPS: *{tpsf:.1f}%*",
            {"placa": placa})

    if abs(accYf) > 0.40 and vel > 25:
        enviar_alerta("CURVA",
            f"🚗 *{placa}*\n📐 Curva brusca\n"
            f"AccY: *{accYf:.2f}g*",
            {"placa": placa})

    if tempf >= 109:
        enviar_alerta("TEMPERATURA",
            f"🚗 *{placa}*\n🌡️ Temperatura crítica *{tempf:.1f}°C*",
            {"placa": placa})

    ultimo_vel[placa] = vel

    try:
        cursor.execute("""
            SELECT km_total, consumo_acum, fecha
            FROM telemetria WHERE placa = %s
            ORDER BY id DESC LIMIT 1
        """, (placa,))
        row = cursor.fetchone()
        if row:
            km_actual    = float(row[0])
            consumo_acum = float(row[1])
            fecha_ultimo = row[2]
            segundos_real = (datetime.now() - fecha_ultimo).total_seconds()
            segundos_real = max(0.5, min(segundos_real, 10))
        else:
            km_actual    = 0
            consumo_acum = 0
            segundos_real = 2

        tiempo_h      = segundos_real / 3600.0
        km_nuevo      = km_actual + (vel * tiempo_h)
        consumo_ciclo = calcular_consumo(
            rpm=rpm, map_kpa=map_pressure,
            iat_c=iat, tps=tps,
            engine_load=engine_load,
            segundos=segundos_real)
        consumo_nuevo = consumo_acum + consumo_ciclo

    except Exception as e:
        print("⚠ Error cálculo:", e)
        km_nuevo = consumo_ciclo = consumo_nuevo = 0

    try:
        sql = """
        INSERT INTO telemetria (
            placa, rpm, velocidad, temperatura,
            tps, map_pressure, engine_load, iat,
            accX, accY, accZ, angleX, angleY,
            km_total, consumo_gal, consumo_acum
        ) VALUES (
            %s,%s,%s,%s,%s,%s,%s,%s,
            %s,%s,%s,%s,%s,%s,%s,%s
        )
        """
        valores = (
            placa, rpm, velocidad, temperatura,
            tps, map_pressure, engine_load, iat,
            accX, accY, accZ, angleX, angleY,
            km_nuevo, consumo_ciclo, consumo_nuevo
        )
        cursor.execute(sql, valores)
        db.commit()
    except Exception as e:
        print("❌ MySQL:", e)
        return "Error MySQL", 500

    print(f"🚗 {placa} | RPM:{rpm} | VEL:{velocidad} | "
          f"KM:{km_nuevo:.3f} | GAL:{consumo_nuevo:.5f}")
    return "OK", 200

# =========================
# RESET KM
# =========================
@app.route('/reset_km')
def reset_km():
    placa = request.args.get("placa", None)
    try:
        if placa:
            cursor.execute("""
                INSERT INTO telemetria (
                    placa,rpm,velocidad,temperatura,tps,
                    map_pressure,engine_load,iat,
                    accX,accY,accZ,angleX,angleY,
                    km_total,consumo_gal,consumo_acum
                ) VALUES (%s,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
            """, (placa,))
        else:
            cursor.execute("""
                INSERT INTO telemetria (
                    placa,rpm,velocidad,temperatura,tps,
                    map_pressure,engine_load,iat,
                    accX,accY,accZ,angleX,angleY,
                    km_total,consumo_gal,consumo_acum
                ) VALUES ('RESET',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
            """)
        db.commit()
        return "Reseteado", 200
    except Exception as e:
        return f"Error: {e}", 500

# =========================
# MAIN
# =========================
if __name__ == '__main__':
    t = threading.Thread(target=polling_telegram, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=5000, debug=False)
