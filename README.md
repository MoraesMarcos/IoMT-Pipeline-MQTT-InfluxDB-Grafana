# IoMT — MQTT → InfluxDB → Grafana Pipeline

Reimplementation of the data pipeline using MQTT for real (instead of the
direct HTTP that was in production), to align with the architecture
described in the paper, plus an increasing-load experiment measuring
latency, jitter, message loss, CPU and memory usage.

> ⚠️ **Status:** all the code below was written and validated locally
> (syntax compiled, calculation functions tested in isolation), but **has
> not yet been executed against the real Mosquitto/InfluxDB on the server**
> (`200.133.17.234`), due to network/VPN unavailability at the time of
> writing. Before citing any value in the paper as an experimental result,
> run the [End-to-end validation](#end-to-end-validation) section and the
> [load experiment](#increasing-load-experiment) on the actual server.

## Repository structure

```
iomt-mqtt/
├── firmware/
│   └── firmware_mqtt.ino       # ESP8266, publishes via MQTT (PubSubClient)
├── bridge/
│   ├── mqtt_influx_bridge.py   # Subscribes to MQTT, writes to InfluxDB 1.6.7
│   ├── mqtt-influx-bridge.service
│   └── requirements.txt
├── load-test/
│   ├── load_test.py            # Publishes at increasing rate, measures RTT/jitter/loss
│   ├── echo_responder.py       # Acknowledges receipt so RTT can be measured
│   ├── resource_monitor.py     # Samples server CPU/memory during the test
│   └── requirements.txt
└── .gitignore
```

## Context: why MQTT instead of HTTP

The original firmware sent data via `ESP8266HTTPClient`, with a direct POST
to the InfluxDB REST API — with no MQTT broker in between, even though the
paper describes this step as a central part of the architecture (Section 5,
Figures 2 and 4). The `mqtt_data` database already existed under that name,
but it was not actually being fed by an MQTT pipeline: Mosquitto was
installed and running on the server, but with no publisher or subscriber
connected to it.

This directory fixes that mismatch: the firmware now publishes over MQTT
for real, and a dedicated bridge handles the MQTT → InfluxDB hop.

## Server environment

| Item | Value |
|---|---|
| vCPUs | 4 |
| RAM | 1.9 GiB |
| Hypervisor | KVM (QEMU, generic hardware "Standard PC i440FX + PIIX") |
| OS | Debian GNU/Linux 12 (bookworm), kernel 5.10.0-10-amd64 |
| InfluxDB | 1.6.7, `auth-enabled = false` |
| MQTT broker | Mosquitto, default configuration (`/etc/mosquitto/conf.d/`) |
| Grafana | active, dashboards under `Dashboards > MIMIC` |

> Still to confirm: whether the VM is self-hosted (local server) or hosted
> by a cloud provider — the hostname (`pmr-srv-valentim`) and the generic
> QEMU hardware suggest self-hosted infrastructure, but this has not been
> explicitly confirmed.

## Components

- **`firmware/firmware_mqtt.ino`** — ESP8266 firmware that publishes the
  samples (BPM/SpO2) over MQTT using the `PubSubClient` library, on the
  `iomt/paciente/dados` topic. The `dados[]` array (patient samples) was
  omitted for brevity in this repository — copy it from the original HTTP
  firmware before compiling (see [instructions below](#how-to-flashupdate-the-firmware)).
- **`bridge/mqtt_influx_bridge.py`** — Python script that subscribes to the
  MQTT topic and writes the points to InfluxDB 1.6.7 (the classic
  `influxdb` library, not `influxdb-client`, which targets v2.x), with
  configurable batching. Since InfluxDB 1.x has no built-in time-based
  flush in the client, the `flush_interval` batching was implemented
  manually via a separate thread (`flusher_loop`).
- **`bridge/mqtt-influx-bridge.service`** — systemd unit to run the bridge
  as a persistent service, restarting automatically on failure.
- **`load-test/`** — three scripts for the increasing-load experiment
  (details in its own section below).

## Configuration parameters

| Parameter | Value | Defined in |
|---|---|---|
| MQTT QoS (publish) | 0 | `firmware_mqtt.ino` — limitation of the PubSubClient library, which only publishes at QoS 0 |
| MQTT QoS (bridge subscription) | 1 | `mqtt_influx_bridge.py`, `MQTT_SUBSCRIBE_QOS` |
| Broker keep-alive | 60 s | `firmware_mqtt.ino` (`MQTT_KEEPALIVE_S`) and `mqtt_influx_bridge.py` (`MQTT_KEEPALIVE_S`) |
| InfluxDB write batch size | 50 points | `mqtt_influx_bridge.py`, `INFLUX_BATCH_SIZE` |
| InfluxDB flush interval | 2000 ms | `mqtt_influx_bridge.py`, `INFLUX_FLUSH_INTERVAL_MS` |
| Grafana panel auto-refresh | 5 s | Confirmed directly on the Grafana dashboard |

> **Note on QoS:** the effective end-to-end QoS of the pipeline is 0,
> because the PubSubClient library used on the ESP8266 does not actually
> implement QoS 1/2 in `publish()` — even if the constant is declared in
> the code, there is no real ACK handshake. If true QoS 1/2 is required,
> switch the firmware library to `AsyncMqttClient` or `espMqttClient`.
> Documenting this limitation in the paper is more correct than claiming
> QoS 1 when the publisher's actual behavior doesn't guarantee it.

Adjust `INFLUX_BATCH_SIZE` and `INFLUX_FLUSH_INTERVAL_MS` based on the
results of the load experiment below — these are the two parameters that
most affect the throughput vs. write-latency trade-off in InfluxDB.

## How to deploy on the server

```bash
# 1. Copy the whole project to the server (firmware + bridge + load-test)
scp -r iomt-mqtt/ mvvm@200.133.17.234:/home/mvvm/iomt-mqtt

# 2. On the server, install the bridge's dependencies
cd /home/mvvm/iomt-mqtt/bridge
pip3 install -r requirements.txt --break-system-packages

# 3. Edit mqtt_influx_bridge.py and fill in, IF the server requires auth:
#    INFLUX_USERNAME, INFLUX_PASSWORD
#    (auth-enabled=false on InfluxDB today, so you can leave these as None)

# 4. Test manually before turning it into a service
python3 mqtt_influx_bridge.py
# (leave it running, publish one sample via the firmware, and check it shows up in Grafana)

# 5. Install as a systemd service
sudo cp mqtt-influx-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mqtt-influx-bridge
sudo systemctl status mqtt-influx-bridge
```

## How to flash/update the firmware

1. Open `firmware/firmware_mqtt.ino` in the Arduino IDE.
2. Install the `PubSubClient` library (Sketch > Include Library > Manage Libraries).
3. Copy the full `dados[]` array from the original HTTP firmware into this
   file — it was omitted here for brevity, but the values are identical.
4. Adjust `rede`, `senha`, `mqtt_server` if needed.
5. Upload to the ESP8266.

## End-to-end validation

After deploying the bridge and the firmware:

```bash
# Confirm Mosquitto is receiving messages (separate terminal)
mosquitto_sub -h 200.133.17.234 -t "iomt/paciente/dados" -v

# Confirm the bridge is writing to InfluxDB
sudo journalctl -u mqtt-influx-bridge -f
```

If both show live activity, the MQTT → InfluxDB → Grafana pipeline is
functional and the parameters in the table above become real,
paper-citable values.

## Increasing-load experiment

`load-test/` folder — replicates the latency/jitter methodology from
Section 6.4 of the paper (L_i = T_end − T_start, RTT), but varying the
publication rate (messages/s) across several levels, and also recording
server CPU and memory during each level.

### Components

- **`load_test.py`** — publishes messages at an increasing rate
  (`LOAD_LEVELS`, editable) and measures latency (RTT), jitter and message
  loss, writing one CSV per load level plus a consolidated `summary.csv`.
- **`echo_responder.py`** — needed because the real bridge
  (`mqtt_influx_bridge.py`) only writes to InfluxDB and doesn't acknowledge
  receipt. This script subscribes to the test topic and "echoes" each
  message back, allowing RTT to be measured the same way as the original
  Section 6.4 experiment.
- **`resource_monitor.py`** — samples CPU/memory (via `psutil`) every 1s
  during the test, including individual CPU usage for the `mosquitto`,
  `influxd`, `grafana-server` and `python3` (the bridge itself) processes.

### How to run (3 simultaneous SSH terminals on the server)

```bash
# Install dependencies (once)
cd /home/mvvm/iomt-mqtt/load-test
pip3 install -r requirements.txt --break-system-packages

# Terminal 1: resource monitor (leave running the whole time)
python3 resource_monitor.py

# Terminal 2: echo responder (leave running the whole time)
python3 echo_responder.py

# Terminal 3: triggers the load test (runs and finishes on its own)
python3 load_test.py
```

At the end, `load_test.py` writes to `load-test/results/<timestamp>/`:
- `load_1mps.csv`, `load_5mps.csv`, ... — raw latencies for each level
- `summary.csv` — min/mean/median/p95/p99/max/stdev latency, mean/stdev
  jitter, and loss rate, one row per load level

`resource_monitor.py` writes separately to
`load-test/results/resources_<timestamp>.csv` — to cross-reference with
the load test's `summary.csv`, align by timestamp (each load level runs
for `DURATION_PER_LEVEL_S` seconds, editable in the script).

### Adjusting load levels

Edit `LOAD_LEVELS` in `load_test.py` (default: `[1, 5, 10, 20, 50]` msg/s,
60s each). Start with values close to the original (the paper used ~1
transaction every 500ms ≈ 2 msg/s) and increase gradually until you observe
latency degradation, message loss, or CPU saturation — this defines the
practical capacity limit of the current server (4 vCPU / 1.9 GB RAM).
- [ ] Confirm whether the VM is self-hosted or cloud-hosted, to complete
      the "Server environment" section above
- [ ] `git add . && git commit && git push` to publish these changes
