# IoMT — Pipeline MQTT → InfluxDB → Grafana

Este diretório contém a reimplementação do pipeline de dados usando MQTT de
fato (em vez do HTTP direto que estava em produção), para ficar alinhado com
a arquitetura descrita no artigo.

> **Versão do InfluxDB confirmada no servidor: 1.6.7.** O bridge usa a
> biblioteca clássica `influxdb` (não `influxdb-client`, que é para a v2.x) e
> grava no database `mqtt_data`, o mesmo já usado hoje. O InfluxDB 1.x não
> tem flush automático por tempo no cliente, então o batching por
> `flush_interval` foi implementado manualmente com uma thread separada no
> bridge (`flusher_loop`).

## Componentes

- `firmware/firmware_mqtt.ino` — Firmware do ESP8266 que publica as amostras
  (BPM/SpO2) via MQTT usando a lib `PubSubClient`, no tópico `iomt/paciente/dados`.
- `bridge/mqtt_influx_bridge.py` — Script Python que subscreve o tópico MQTT
  e grava os pontos no InfluxDB, com batching configurável.
- `bridge/mqtt-influx-bridge.service` — Unit systemd para rodar o bridge
  como serviço persistente no servidor.

## Parâmetros de configuração (para a tabela do artigo)

| Parâmetro                          | Valor          | Onde está definido |
|-------------------------------------|----------------|---------------------|
| MQTT QoS (publicação)                | 0              | `firmware_mqtt.ino` — limitação da lib PubSubClient, que só publica em QoS 0 |
| MQTT QoS (subscrição do bridge)      | 1              | `mqtt_influx_bridge.py`, `MQTT_SUBSCRIBE_QOS` |
| Broker keep-alive                    | 60 s           | `firmware_mqtt.ino` (`MQTT_KEEPALIVE_S`) e `mqtt_influx_bridge.py` (`MQTT_KEEPALIVE_S`) |
| InfluxDB write batch size            | 50 pontos      | `mqtt_influx_bridge.py`, `INFLUX_BATCH_SIZE` |
| InfluxDB flush interval              | 2000 ms        | `mqtt_influx_bridge.py`, `INFLUX_FLUSH_INTERVAL_MS` |
| Grafana panel auto-refresh           | 5 s            | Confirmado direto no dashboard do Grafana |

> **Nota sobre QoS**: o valor efetivo ponta-a-ponta do pipeline é QoS 0,
> porque a biblioteca PubSubClient usada no ESP8266 não implementa QoS 1/2 de
> verdade em `publish()`. Se for necessário QoS 1/2 real (com ACK garantido),
> troque a lib do firmware para `AsyncMqttClient` ou `espMqttClient`.
> Documentar essa limitação no artigo é mais correto do que declarar QoS 1
> sem que o comportamento real do publisher garanta isso.

Ajuste `INFLUX_BATCH_SIZE` e `INFLUX_FLUSH_INTERVAL_MS` conforme os testes de
carga do item 4 — esses são os dois parâmetros que mais afetam
throughput vs. latência de escrita no InfluxDB.

## Como implantar no servidor

```bash
# 1. Copiar os arquivos do bridge para o servidor
scp -r bridge/ mvvm@200.133.17.234:/home/mvvm/iomt-mqtt/bridge

# 2. No servidor, instalar dependências Python
cd /home/mvvm/iomt-mqtt/bridge
pip install -r requirements.txt --break-system-packages

# 3. Editar mqtt_influx_bridge.py e preencher, se o servidor exigir auth:
#    INFLUX_USERNAME, INFLUX_PASSWORD
#    (InfluxDB 1.6.7 usa database/usuário/senha -- não tem token/org/bucket)

# 4. Testar manualmente antes de virar serviço
python3 mqtt_influx_bridge.py
# (deixe rodando, publique uma amostra pelo firmware e confira se aparece no Grafana)

# 5. Instalar como serviço systemd
sudo cp mqtt-influx-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mqtt-influx-bridge
sudo systemctl status mqtt-influx-bridge
```

## Como gravar/atualizar o firmware

1. Abra `firmware/firmware_mqtt.ino` na Arduino IDE.
2. Instale a lib `PubSubClient` (Sketch > Include Library > Manage Libraries).
3. Copie o array `dados[]` completo do firmware original (`main.ino`) para
   dentro deste arquivo — foi omitido aqui por brevidade, mas os valores são
   idênticos.
4. Ajuste `rede`, `senha`, `mqtt_server` se necessário.
5. Faça o upload para o ESP8266.

## Validação end-to-end

Depois de subir o bridge e o firmware:

```bash
# Confirmar que o Mosquitto recebe mensagens (terminal separado)
mosquitto_sub -h 200.133.17.234 -t "iomt/paciente/dados" -v

# Confirmar que o bridge está escrevendo no InfluxDB
sudo journalctl -u mqtt-influx-bridge -f
```

Se ambos mostrarem atividade em tempo real, o pipeline MQTT → InfluxDB →
Grafana está funcional e os parâmetros acima passam a ser os valores reais e
citáveis no artigo.
