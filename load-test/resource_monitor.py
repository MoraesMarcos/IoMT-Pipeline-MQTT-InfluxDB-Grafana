#!/usr/bin/env python3
"""
Monitor de utilização de CPU e memória, para rodar no servidor durante os
testes de carga (load_test.py).

Amostra CPU% (global e por processo dos serviços relevantes) e memória a
cada SAMPLE_INTERVAL_S, gravando em CSV. Rode em paralelo com load_test.py
e echo_responder.py -- idealmente em uma sessão SSH/tmux separada, para que
o monitor não seja interrompido junto com o teste de carga.

Uso:
    python3 resource_monitor.py

Encerre com Ctrl+C; o CSV é gravado incrementalmente, então dados parciais
não se perdem se você interromper no meio.
"""

import csv
import os
import time
from datetime import datetime

import psutil

SAMPLE_INTERVAL_S = 1.0

RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
CSV_PATH = os.path.join(RESULTS_DIR, f"resources_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")

# Nomes de processo relevantes para acompanhar individualmente (ajuste se
# os nomes reais no seu servidor forem diferentes -- confira com `ps aux`).
TRACKED_PROCESS_NAMES = ["mosquitto", "influxd", "grafana-server", "python3"]


def find_tracked_pids():
    pids = {}
    for proc in psutil.process_iter(["pid", "name"]):
        name = proc.info["name"] or ""
        for tracked in TRACKED_PROCESS_NAMES:
            if tracked in name:
                pids.setdefault(tracked, []).append(proc.info["pid"])
    return pids


def main():
    print(f"[monitor] Gravando amostras em: {CSV_PATH}")
    print("[monitor] Ctrl+C para parar.")

    fieldnames = [
        "timestamp",
        "cpu_percent_total",
        "mem_percent",
        "mem_used_mb",
        "mem_available_mb",
    ] + [f"cpu_percent_{name}" for name in TRACKED_PROCESS_NAMES]

    # "Aquece" o cálculo de cpu_percent (a primeira chamada retorna 0.0)
    psutil.cpu_percent(interval=None)

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        try:
            while True:
                time.sleep(SAMPLE_INTERVAL_S)

                cpu_total = psutil.cpu_percent(interval=None)
                mem = psutil.virtual_memory()

                row = {
                    "timestamp": datetime.now().isoformat(),
                    "cpu_percent_total": cpu_total,
                    "mem_percent": mem.percent,
                    "mem_used_mb": round(mem.used / (1024 * 1024), 2),
                    "mem_available_mb": round(mem.available / (1024 * 1024), 2),
                }

                pids_by_name = find_tracked_pids()
                for name in TRACKED_PROCESS_NAMES:
                    total_cpu_for_name = 0.0
                    for pid in pids_by_name.get(name, []):
                        try:
                            total_cpu_for_name += psutil.Process(pid).cpu_percent(interval=None)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                    row[f"cpu_percent_{name}"] = round(total_cpu_for_name, 2)

                writer.writerow(row)
                f.flush()

        except KeyboardInterrupt:
            print(f"\n[monitor] Encerrado. Dados salvos em: {CSV_PATH}")


if __name__ == "__main__":
    main()
