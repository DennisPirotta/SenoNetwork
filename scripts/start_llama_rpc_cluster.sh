#!/bin/bash
# ==============================================================================
# Script di avvio per Cluster llama.cpp con Inferenza Distribuita via RPC su 2 Schede
# ==============================================================================
#
# Architettura:
# 1. Scheda GPU 0 -> Avvia rpc-server sulla porta 50052
# 2. Scheda GPU 1 -> Avvia rpc-server sulla porta 50053
# 3. Master Server -> Avvia llama-server con parametro --rpc 127.0.0.1:50052,127.0.0.1:50053
#                     e porta HTTP 8080 (endpoint compatibile OpenAI /v1/chat/completions)
# ==============================================================================

set -e

MODEL_PATH="${1:-/path/to/model.gguf}"
HOST_MASTER="0.0.0.0"
PORT_MASTER=8080
RPC_PORT_1=50052
RPC_PORT_2=50053
CONTEXT_SIZE=8192

echo "========================================================================"
echo "Avvio Cluster llama.cpp Distribuito (2 Schede via Protocollo RPC)"
echo "========================================================================"
echo "Modello: $MODEL_PATH"
echo "Porta Master HTTP: $PORT_MASTER"
echo "RPC Scheda 0: 127.0.0.1:$RPC_PORT_1"
echo "RPC Scheda 1: 127.0.0.1:$RPC_PORT_2"
echo "========================================================================"

# Verifica esistenza binari llama.cpp
if ! command -v rpc-server &> /dev/null; then
    echo "ATTENZIONE: 'rpc-server' non trovato nel PATH. Compilare llama.cpp con -DGGML_RPC=ON."
fi

if ! command -v llama-server &> /dev/null; then
    echo "ATTENZIONE: 'llama-server' non trovato nel PATH."
fi

# 1. Avvio Worker RPC per Scheda GPU 0
echo "[1/3] Avvio rpc-server su GPU 0 (porta $RPC_PORT_1)..."
CUDA_VISIBLE_DEVICES=0 rpc-server -H 127.0.0.1 -p $RPC_PORT_1 &
PID_RPC1=$!
echo "-> rpc-server GPU 0 avviato con PID $PID_RPC1"

# 2. Avvio Worker RPC per Scheda GPU 1
echo "[2/3] Avvio rpc-server su GPU 1 (porta $RPC_PORT_2)..."
CUDA_VISIBLE_DEVICES=1 rpc-server -H 127.0.0.1 -p $RPC_PORT_2 &
PID_RPC2=$!
echo "-> rpc-server GPU 1 avviato con PID $PID_RPC2"

# Attesa stabilizzazione porte RPC
sleep 2

# Trap per terminare i worker alla chiusura dello script
trap 'echo "Terminazione cluster RPC..."; kill $PID_RPC1 $PID_RPC2 2>/dev/null || true' EXIT INT TERM

# 3. Avvio Master llama-server
echo "[3/3] Avvio llama-server distribuito sui due nodi RPC..."
llama-server \
    -m "$MODEL_PATH" \
    --rpc "127.0.0.1:$RPC_PORT_1,127.0.0.1:$RPC_PORT_2" \
    --host "$HOST_MASTER" \
    --port "$PORT_MASTER" \
    -c "$CONTEXT_SIZE" \
    --n-gpu-layers 999 \
    --flash-attn \
    --cont-batching
