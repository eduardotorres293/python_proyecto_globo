import time, argparse
from collections import deque
from datetime import datetime, timezone
import psycopg2

from registrar_lecturas import DB, VUELO_ID, SQL_INSERT, asegurar_vuelo, procesar

# ---- Datos de ejemplo (página 16) ----
P_VENTANA, T_VENTANA, HR_VENTANA = 1013.1, 17.9, 74
LECTURAS_CELDA = [197900, 199100, 198400, 200200, 198900]
ADC_BATERIA = 802

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pausa", type=float, default=2.0, help="segundos entre lecturas")
    ap.add_argument("--limpiar", action="store_true", help="borra lecturas previas del vuelo")
    args = ap.parse_args()

    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor()
    asegurar_vuelo(cur)
    cur.execute("UPDATE vuelo SET notas=%s WHERE id=%s",
                ("Caso de prueba de la página 16", VUELO_ID))
    if args.limpiar:
        cur.execute("DELETE FROM lectura WHERE vuelo_id=%s", (VUELO_ID,))

    buf_fh = deque(maxlen=5)
    for i, celda in enumerate(LECTURAS_CELDA, 1):
        raw = dict(P=P_VENTANA, T=T_VENTANA, HR=HR_VENTANA,
                   celda=celda, adc=ADC_BATERIA)
        d = procesar(raw, buf_fh)
        d.update(vuelo=VUELO_ID, ts=datetime.now(timezone.utc))
        cur.execute(SQL_INSERT, d)
        print(f"#{i}  celda={celda}  Fh_prom={d['fh']:.3f} N  v={d['v']:.2f} m/s  "
              f"ráf={d['raf']:.2f}  h={d['h']:.2f} m  ang={d['ang']:.1f}°  "
              f"T={d['ten']:.2f} N  ρ={d['rho']:.4f}  bat={d['bat']:.2f} V")
        time.sleep(args.pausa)

    cur.execute("UPDATE vuelo SET fin=now() WHERE id=%s", (VUELO_ID,))
    conn.close()
    print("Listo. Vuelo cerrado.")

if __name__ == "__main__":
    main()
