# IoMT — Pipeline MQTT → InfluxDB → Grafana

Reimplementação do pipeline de dados usando MQTT de fato (em vez do HTTP
direto que estava em produção), para ficar alinhado com a arquitetura
descrita no artigo, mais um experimento de carga crescente com medição de
latência, jitter, perda de mensagens, CPU e memória.

> ⚠️ **Status:** todo o código abaixo foi escrito e validado localmente
> (sintaxe compilada, funções de cálculo testadas isoladamente), mas **ainda
> não foi executado contra o Mosquitto/InfluxDB reais do servidor**
> (`200.133.17.234`), por indisponibilidade de rede/VPN no momento da
> escrita. Antes de citar qualquer valor no artigo como resultado
> experimental, rode a seção [Validação end-to-end](#validação-end-to-end) e
> o [experimento de carga](#experimento-de-carga-crescente) no servidor de
> fato.

## Estrutura do repositório

```
iomt-mqtt/
├── firmware/
│   └── firmware_mqtt.ino       # ESP8266, publica via MQTT (PubSubClient)
├── bridge/
│   ├── mqtt_influx_bridge.py   # Subscreve MQTT, grava no InfluxDB 1.6.7
│   ├── mqtt-influx-bridge.service
│   └── requirements.txt
├── load-test/
│   ├── load_test.py            # Publica em taxa crescente, mede RTT/jitter/perda
│   ├── echo_responder.py       # Confirma recebimento p/ permitir medir RTT
│   ├── resource_monitor.py     # Amostra CPU/memória do servidor durante o teste
│   └── requirements.txt
└── .gitignore
```

## Contexto: por que MQTT em vez de HTTP

O firmware original enviava os dados via `ESP8266HTTPClient`, com POST
direto para a API REST do InfluxDB — sem broker MQTT no meio, apesar do
artigo descrever essa etapa como parte central da arquitetura (Seção 5,
Figura 2 e 4). O banco `mqtt_data` já existia com esse nome, mas não estava
sendo usado por um pipeline MQTT de fato: o Mosquitto estava instalado e
ativo no servidor, porém sem nenhum publisher ou subscriber conectado a ele.

Este diretório corrige essa divergência: o firmware agora publica via MQTT
de verdade, e um bridge dedicado faz a ponte MQTT → InfluxDB.

## Ambiente do servidor (confirmado via SSH)

| Item | Valor |
|---|---|
| vCPUs | 4 |
| RAM | 1.9 GiB |
| Hypervisor | KVM (QEMU, hardware genérico "Standard PC i440FX + PIIX") |
| SO | Debian GNU/Linux 12 (bookworm), kernel 5.10.0-10-amd64 |
| InfluxDB | 1.6.7, `auth-enabled = false` |
| Broker MQTT | Mosquitto, configuração padrão (`/etc/mosquitto/conf.d/` vazio) |
| Grafana | ativo, dashboards em `Dashboards > MIMIC` |

> Falta confirmar: se a VM é hospedagem local/própria ou provedor de nuvem
> — o hostname (`pmr-srv-valentim`) e o hardware genérico QEMU sugerem
> infraestrutura própria, mas isso não foi confirmado explicitamente.

## Componentes

- **`firmware/firmware_mqtt.ino`** — Firmware do ESP8266 que publica as
  amostras (BPM/SpO2) via MQTT usando a lib `PubSubClient`, no tópico
  `iomt/paciente/dados`. O array `dados[]` (amostras dos pacientes) foi
  omitido por brevidade neste repositório — copie-o do firmware HTTP
  original antes de compilar (ver [instruções abaixo](#como-gravaratualizar-o-firmware)).
- **`bridge/mqtt_influx_bridge.py`** — Script Python que subscreve o tópico
  MQTT e grava os pontos no InfluxDB 1.6.7 (biblioteca clássica `influxdb`,
  não `influxdb-client`, que é para a v2.x), com batching configurável.
  Como o InfluxDB 1.x não tem flush automático por tempo no cliente, o
  batching por `flush_interval` foi implementado manualmente com uma thread
  separada (`flusher_loop`).
- **`bridge/mqtt-influx-bridge.service`** — Unit systemd para rodar o bridge
  como serviço persistente, reiniciando automaticamente em caso de falha.
- **`load-test/`** — três scripts para o experimento de carga crescente
  (detalhes na seção própria abaixo).

## Parâmetros de configuração (para a tabela do artigo)

| Parâmetro | Valor | Onde está definido |
|---|---|---|
| MQTT QoS (publicação) | 0 | `firmware_mqtt.ino` — limitação da lib PubSubClient, que só publica em QoS 0 |
| MQTT QoS (subscrição do bridge) | 1 | `mqtt_influx_bridge.py`, `MQTT_SUBSCRIBE_QOS` |
| Broker keep-alive | 60 s | `firmware_mqtt.ino` (`MQTT_KEEPALIVE_S`) e `mqtt_influx_bridge.py` (`MQTT_KEEPALIVE_S`) |
| InfluxDB write batch size | 50 pontos | `mqtt_influx_bridge.py`, `INFLUX_BATCH_SIZE` |
| InfluxDB flush interval | 2000 ms | `mqtt_influx_bridge.py`, `INFLUX_FLUSH_INTERVAL_MS` |
| Grafana panel auto-refresh | 5 s | Confirmado direto no dashboard do Grafana |

> **Nota sobre QoS:** o valor efetivo ponta-a-ponta do pipeline é QoS 0,
> porque a biblioteca PubSubClient usada no ESP8266 não implementa QoS 1/2
> de verdade em `publish()` — mesmo que se declare a constante no código,
> não há handshake de ACK real. Se for necessário QoS 1/2 de fato, troque a
> lib do firmware para `AsyncMqttClient` ou `espMqttClient`. Documentar essa
> limitação no artigo é mais correto do que declarar QoS 1 sem que o
> comportamento real do publisher garanta isso.

Ajuste `INFLUX_BATCH_SIZE` e `INFLUX_FLUSH_INTERVAL_MS` conforme os
resultados do experimento de carga abaixo — são os dois parâmetros que mais
afetam o trade-off entre throughput e latência de escrita no InfluxDB.

## Como implantar no servidor

```bash
# 1. Copiar o projeto inteiro para o servidor (firmware + bridge + load-test)
scp -r iomt-mqtt/ mvvm@200.133.17.234:/home/mvvm/iomt-mqtt

# 2. No servidor, instalar as dependências do bridge
cd /home/mvvm/iomt-mqtt/bridge
pip3 install -r requirements.txt --break-system-packages

# 3. Editar mqtt_influx_bridge.py e preencher, SE o servidor exigir auth:
#    INFLUX_USERNAME, INFLUX_PASSWORD
#    (hoje auth-enabled=false no InfluxDB, então pode deixar como None)

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
3. Copie o array `dados[]` completo do firmware HTTP original para dentro
   deste arquivo — foi omitido aqui por brevidade, mas os valores são
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
Grafana está funcional e os parâmetros da tabela acima passam a ser valores
reais e citáveis no artigo.

## Experimento de carga crescente

Pasta `load-test/` — repete a metodologia de latência/jitter da Seção 6.4 do
artigo (L_i = T_end − T_start, RTT), mas variando a taxa de publicação
(mensagens/s) em vários níveis, e registrando também CPU e memória do
servidor durante cada nível.

### Componentes

- **`load_test.py`** — publica mensagens em taxa crescente (`LOAD_LEVELS`,
  editável) e mede latência (RTT), jitter e perda de mensagens, gravando um
  CSV por nível de carga e um `summary.csv` consolidado.
- **`echo_responder.py`** — necessário porque o bridge real
  (`mqtt_influx_bridge.py`) só escreve no InfluxDB e não confirma
  recebimento. Este script assina o tópico de teste e "ecoa" cada mensagem
  de volta, permitindo medir RTT do mesmo jeito que o experimento original
  da Seção 6.4.
- **`resource_monitor.py`** — amostra CPU/memória (via `psutil`) a cada 1s
  durante o teste, incluindo CPU individual dos processos `mosquitto`,
  `influxd`, `grafana-server` e `python3` (o próprio bridge).

### Como rodar (3 terminais SSH simultâneos no servidor)

```bash
# Instalar dependências (uma vez)
cd /home/mvvm/iomt-mqtt/load-test
pip3 install -r requirements.txt --break-system-packages

# Terminal 1: monitor de recursos (deixe rodando o tempo todo)
python3 resource_monitor.py

# Terminal 2: echo responder (deixe rodando o tempo todo)
python3 echo_responder.py

# Terminal 3: dispara o teste de carga (roda e termina sozinho)
python3 load_test.py
```

Ao final, `load_test.py` grava em `load-test/results/<timestamp>/`:
- `load_1mps.csv`, `load_5mps.csv`, ... — latências brutas de cada nível
- `summary.csv` — min/mean/median/p95/p99/max/stdev de latência, jitter
  médio e stdev, e taxa de perda, uma linha por nível de carga

O `resource_monitor.py` grava separadamente em
`load-test/results/resources_<timestamp>.csv` — para cruzar com o
`summary.csv` do teste de carga, alinhe pelos timestamps (cada nível de
carga roda por `DURATION_PER_LEVEL_S` segundos, editável no script).

### Ajustando os níveis de carga

Edite `LOAD_LEVELS` em `load_test.py` (padrão: `[1, 5, 10, 20, 50]` msg/s,
60s cada). Comece com valores próximos ao original (o artigo usou ~1
transação a cada 500ms ≈ 2 msg/s) e aumente gradualmente até observar
degradação de latência, perda de mensagens, ou saturação de CPU — isso
define o limite prático de capacidade do servidor atual (4 vCPU / 1.9 GB
RAM).

## Pendências

- [ ] Copiar o projeto para o servidor via `scp` (bloqueado no momento por
      indisponibilidade de rede/VPN até `200.133.17.234`)
- [ ] Colar o array `dados[]` completo dentro de `firmware_mqtt.ino`
- [ ] Rodar a validação end-to-end (Mosquitto + bridge + Grafana)
- [ ] Rodar o experimento de carga e anexar os CSVs de resultado a este
      repositório (ou a uma pasta `load-test/results/` versionada)
- [ ] Confirmar se a VM é hospedagem local ou nuvem, para completar a seção
      "Ambiente do servidor" acima
- [ ] `git add . && git commit && git push` para publicar estas mudanças
