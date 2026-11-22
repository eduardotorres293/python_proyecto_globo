import math, time, random, argparse
from collections import deque
from datetime import datetime, timezone
import psycopg2

# CONFIGURACION
DB = dict(host="localhost", port=5432, dbname="postgres",
          user="postgres", password="1234")
VUELO_ID = 1            # el panel lee el vuelo 1
LONGITUD = 40.0         # m de cuerda
INTERVALO_S = 1.0       # segundos entre lecturas

g = 9.80665
r_d = 287.05

# FUNCIONES DEL PROGRAMA
def aire(t_c, p_hpa, hr):
    t_k = t_c + 273.15
    e = 6.1078 * 10 ** ((7.5 * t_c) / (t_c + 237.3))
    pv = e * hr / 100
    tv = t_k / (1 - 0.378 * (pv / p_hpa))
    rho = (100 * p_hpa) / (r_d * tv)
    return tv, rho

def promedio(l):
    return sum(l) / len(l)

def altitud(p0, p, tv0, tv):
    return (r_d * (tv0 + tv) / 2 / g) * math.log(p0 / p)

def fuerza_medida(lectura, l_cero, factor):
    return (lectura - l_cero) / factor * g

def fuerza_horizontal(f, desal_deg=0):
    return f / math.cos(math.radians(desal_deg))

def fuerza_ventana(lista_fh, muestras_por_seg=1):
    f_prom = promedio(lista_fh)
    n3 = max(1, int(3 * muestras_por_seg))
    if len(lista_fh) >= n3:
        f_3s = max(promedio(lista_fh[i:i+n3])
                   for i in range(len(lista_fh) - n3 + 1))
    else:
        f_3s = f_prom
    return f_prom, f_3s

def viento(f, rho, cda):
    return 0.0 if f <= 0 else math.sqrt(2 * f / (rho * cda))

def cuerda(f_h, f_v, altura, longitud):
    tension = math.sqrt(f_h**2 + f_v**2)
    angulo = math.degrees(math.asin(min(1.0, max(0.0, altura / longitud))))
    return tension, angulo

def bateria(adc, v_ref=5, r1=10, r2=10):
    return adc * v_ref / 1023 * (r1 + r2) / r2

# PARAMETROS DEL VUELO
P0, T0, HR0 = 1015.2, 18.5, 72
L_CERO, FACTOR = 120340, 214800
N_GLOBOS, DIAMETRO, CD = 2, 0.80, 0.47
CDA = CD * N_GLOBOS * math.pi * DIAMETRO**2 / 4
DESALINEACION = 10
FV = 1.80
tv0, rho0 = aire(T0, P0, HR0)

#SENSORES SIMULADOS, SE CAMBIAN POR DATOS REALES CUANDO SE TENGA LA LECTURA
estado = {"t": 0, "rafaga": 0.0, "adc": 802}

def leer_sensores(forzar_rafaga=False):
    s = estado
    s["t"] += 1
    t = s["t"]
    if forzar_rafaga:
        s["rafaga"] = 4.5
    s["rafaga"] *= 0.86
    v_real = max(0.3, 3.5 + 1.1*math.sin(t/9) + 0.5*math.sin(t/2.3)
                 + random.gauss(0, .15) + s["rafaga"])
    fh_real = 0.5 * 1.2058 * CDA * v_real**2
    h_real = LONGITUD * math.sin(math.atan(FV / fh_real))
    p = P0 * (1 - h_real/44330) ** (1/0.1903) + random.gauss(0, .03)
    temp = T0 - 0.0065*h_real + random.gauss(0, .05)
    hr = min(100, HR0 + 0.1*h_real + random.gauss(0, .2))
    fm = fh_real * math.cos(math.radians(DESALINEACION))
    celda = round(L_CERO + fm/g*FACTOR + random.gauss(0, 45))
    s["adc"] = max(560, s["adc"] - random.choice([0, 0, 0, 1]))
    return dict(P=p, T=temp, HR=hr, celda=celda, adc=s["adc"])

# INSERCION DE DATOS EN LA BASE DE DATOS
SQL_INSERT = """
INSERT INTO lectura (vuelo_id, ts, presion_hpa, temperatura_c, humedad_rel_pct,
    densidad_kg_m3, altitud_m, fuerza_h_n, angulo_cuerda_deg, tension_n,
    viento_ms, rafaga_ms, viento_ref_ms, bateria_v)
VALUES (%(vuelo)s, %(ts)s, %(P)s, %(T)s, %(HR)s, %(rho)s, %(h)s, %(fh)s,
        %(ang)s, %(ten)s, %(v)s, %(raf)s, %(vref)s, %(bat)s)
ON CONFLICT (vuelo_id, ts) DO NOTHING
"""

def procesar(raw, buf_fh):
    tv, rho = aire(raw["T"], raw["P"], raw["HR"])
    h = altitud(P0, raw["P"], tv0, tv)
    fm = fuerza_medida(raw["celda"], L_CERO, FACTOR)
    fh = fuerza_horizontal(fm, DESALINEACION)
    buf_fh.append(fh)
    fh_prom, fh_3s = fuerza_ventana(list(buf_fh), 1 / INTERVALO_S)
    v = viento(fh_prom, rho, CDA)
    raf = viento(fh_3s, rho, CDA)
    ten, ang = cuerda(fh_prom, FV, h, LONGITUD)
    th = math.radians(ang)
    vref = math.sqrt(2*FV / (rho*CDA*math.tan(th))) if th > 0 else None
    return dict(P=raw["P"], T=raw["T"], HR=raw["HR"], rho=rho, h=h, fh=fh_prom,
                ang=ang, ten=ten, v=v, raf=raf, vref=vref,
                bat=bateria(raw["adc"]))

def asegurar_vuelo(cur):
    cur.execute("SELECT 1 FROM vuelo WHERE id=%s", (VUELO_ID,))
    if cur.fetchone():
        cur.execute("UPDATE vuelo SET fin=NULL WHERE id=%s", (VUELO_ID,))
        return
    cur.execute("""
        INSERT INTO vuelo (id, nombre, inicio, lugar, longitud_cuerda_m, n_globos,
            diametro_globo_m, cda_ef_m2, p0_hpa, t0_c, hr0_pct, notas)
        VALUES (%s,'Vuelo de prueba',now(),'Ensenada, B.C.',%s,%s,%s,%s,%s,%s,%s,
                'Datos simulados desde Python')""",
        (VUELO_ID, LONGITUD, N_GLOBOS, DIAMETRO, CDA, P0, T0, HR0))
    cur.execute("SELECT setval(pg_get_serial_sequence('vuelo','id'), "
                "GREATEST((SELECT MAX(id) FROM vuelo),1))")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rafaga-cada", type=int, default=25,
                    help="cada N lecturas mete una ráfaga (0 = nunca)")
    args = ap.parse_args()

    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    asegurar_vuelo(cur)
    buf_fh = deque(maxlen=5)
    n = 0
    print("Registrando lecturas... Ctrl+C para terminar el vuelo.")
    try:
        while True:
            n += 1
            raw = leer_sensores(bool(args.rafaga_cada) and n % args.rafaga_cada == 0)
            d = procesar(raw, buf_fh)
            d.update(vuelo=VUELO_ID, ts=datetime.now(timezone.utc))
            cur.execute(SQL_INSERT, d)
            print(f"#{n:04d} v={d['v']:.2f} m/s  ráf={d['raf']:.2f}  "
                  f"h={d['h']:.1f} m  T={d['ten']:.2f} N  ρ={d['rho']:.4f}  "
                  f"bat={d['bat']:.2f} V")
            time.sleep(INTERVALO_S)
    except KeyboardInterrupt:
        cur.execute("UPDATE vuelo SET fin=now() WHERE id=%s", (VUELO_ID,))
        print("\nVuelo cerrado.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
