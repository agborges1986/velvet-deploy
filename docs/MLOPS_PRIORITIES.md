# Prioridades MLOps — Modelos Velvet sobre Ollama

> Análisis de la infraestructura actual y recomendaciones para optimización,
> seguridad y madurez operativa del despliegue on-premise (EC2 + Docker + Ollama).

---

## Prioridad Alta

### 1. Agregar `OLLAMA_NUM_THREAD` y ajustar `NUM_PARALLEL`

**Problema:** `OLLAMA_NUM_PARALLEL=8` sin definir `OLLAMA_NUM_THREAD` provoca que cada
solicitud paralela intente usar todos los 32 cores, generando contención severa de CPU.

**Impacto:** Rendimiento y estabilidad bajo carga concurrente.

**Recomendación:**

| Modelo     | `NUM_PARALLEL` | `NUM_THREAD` | Lógica                              |
|------------|----------------|--------------|-------------------------------------|
| Velvet-2B  | 4              | 8            | 8 threads × 4 paralelas = 32 cores |
| Velvet-14B | 2              | 16           | 16 threads × 2 paralelas = 32 cores|

**Archivo afectado:** `infra/docker-compose.yml`

```yaml
environment:
  - OLLAMA_HOST=0.0.0.0
  - OLLAMA_MAX_LOADED_MODELS=1
  - OLLAMA_KEEP_ALIVE=10m
  - OLLAMA_NUM_PARALLEL=4        # Ajustar según modelo
  - OLLAMA_NUM_THREAD=8          # AGREGAR — threads por solicitud
```

**Nota:** Considerar perfiles de Docker Compose (`profiles`) o archivos override
para alternar configuración entre Velvet-2B y Velvet-14B.

---

### 2. Aumentar `mem_limit` para acomodar KV cache

**Problema:** El límite actual de 13 GB cubre el modelo Velvet-14B Q4_K_M (~8.5 GB)
pero no deja margen para el KV cache. Con `num_ctx=4096` y solicitudes paralelas,
el KV cache puede consumir varios GB adicionales, provocando OOM kills.

**Impacto:** Evita terminaciones inesperadas del contenedor bajo carga.

**Recomendación:**

```yaml
# Velvet-14B (modelo ~8.5 GB + KV cache + overhead)
mem_limit: 20g
mem_reservation: 12g

# Velvet-2B (modelo ~1.5 GB + KV cache + overhead)
# mem_limit: 8g
# mem_reservation: 4g
```

**Archivo afectado:** `infra/docker-compose.yml`

---

### 3. TLS en Caddy o túnel SSH

**Problema:** Basic Auth sobre HTTP plano (puerto 8080) transmite credenciales
en texto claro. Cualquier interceptación de red expone usuario y contraseña.

**Impacto:** Seguridad de credenciales en tránsito.

**Opción A — TLS automático con dominio (recomendado para producción):**

```caddyfile
velvet.ejemplo.com {
    basicauth /* {
        {$OLLAMA_AUTH_USER} {$OLLAMA_AUTH_HASH}
    }
    reverse_proxy ollama:11434
    log {
        output file /var/log/caddy/access.log
        format json { time_format iso8601 }
    }
}
```

**Opción B — TLS con certificado autofirmado (entornos sin dominio):**

```caddyfile
:8080 {
    tls internal
    basicauth /* {
        {$OLLAMA_AUTH_USER} {$OLLAMA_AUTH_HASH}
    }
    reverse_proxy ollama:11434
}
```

**Opción C — Túnel SSH (mínimo esfuerzo):**

```bash
ssh -i velvet-key.pem -L 8080:localhost:8080 ec2-user@<IP_PUBLICA>
```

**Archivos afectados:** `infra/Caddyfile`, `infra/docker-compose.yml` (puertos)

---

## Prioridad Media

### 4. Aumentar `OLLAMA_KEEP_ALIVE` para producción

**Problema:** Con `KEEP_ALIVE=10m`, el modelo se descarga de RAM tras 10 minutos
de inactividad. La siguiente solicitud paga un cold-start de 15-30 segundos
mientras el modelo se recarga.

**Impacto:** Elimina latencia de cold-start en servicios activos.

**Recomendación:**

```yaml
environment:
  - OLLAMA_KEEP_ALIVE=24h    # Producción con uso regular
  # - OLLAMA_KEEP_ALIVE=-1   # Nunca descargar (si la RAM lo permite)
```

**Archivo afectado:** `infra/docker-compose.yml`

---

### 5. Checksum SHA256 de archivos GGUF

**Problema:** No hay verificación de integridad de los archivos GGUF generados.
Si un archivo se corrompe durante transferencia o almacenamiento, no hay forma
de detectarlo sin reconvertir.

**Impacto:** Reproducibilidad y confianza en los artefactos del modelo.

**Recomendación:** Agregar al final de `infra/conversion/convert_model.sh`:

```bash
# Después de la cuantización
GGUF_FILE="velvet-${SIZE}-cpu-${VERSION}-${QUANT}.gguf"
sha256sum "$GGUF_FILE" > "${GGUF_FILE}.sha256"
echo "[INFO] Checksum generado: ${GGUF_FILE}.sha256"

# En despliegues posteriores, verificar con:
# sha256sum -c "${GGUF_FILE}.sha256"
```

**Archivo afectado:** `infra/conversion/convert_model.sh`

---

### 6. Observabilidad — Prometheus + alertas

**Problema:** No hay monitoreo continuo de métricas. Los problemas de rendimiento
o memoria solo se detectan cuando un usuario reporta lentitud o errores.

**Impacto:** Visibilidad operativa y detección proactiva de problemas.

**Recomendación:** Agregar servicios de monitoreo al stack Docker:

```yaml
# Agregar a infra/docker-compose.yml
prometheus:
  image: prom/prometheus:latest
  container_name: velvet-prometheus
  restart: unless-stopped
  volumes:
    - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
  ports:
    - "9090:9090"

grafana:
  image: grafana/grafana:latest
  container_name: velvet-grafana
  restart: unless-stopped
  ports:
    - "3000:3000"
  depends_on:
    - prometheus
```

**Métricas clave a monitorear:**

| Métrica                  | Fuente                    | Alerta si...              |
|--------------------------|---------------------------|---------------------------|
| RAM del contenedor       | Docker / cAdvisor         | > 85% de `mem_limit`     |
| CPU del contenedor       | Docker / cAdvisor         | > 90% sostenido por 5min |
| Latencia de inferencia   | Logs de Caddy             | p90 > 30s                |
| Modelos cargados         | Ollama `/api/ps`          | 0 modelos (cold state)   |
| Errores HTTP 5xx         | Logs de Caddy             | > 5 en 1 minuto          |

**Archivos nuevos:** `infra/prometheus.yml`, `infra/docker-compose.yml` (servicios adicionales)

---

## Prioridad Baja

### 7. CI/CD para conversión automática

**Problema:** El pipeline de conversión (HF → GGUF → cuantizado → Modelfile → despliegue)
es completamente manual. No hay forma automatizada de detectar nuevas versiones
de Velvet en Hugging Face ni de promover modelos validados a producción.

**Impacto:** Madurez del ciclo MLOps.

**Recomendación — Pipeline GitHub Actions:**

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐    ┌──────────┐
│ Trigger:     │───▶│ Conversión:  │───▶│ Validación: │───▶│ Deploy:  │
│ Nueva versión│    │ HF → GGUF   │    │ Suite tests │    │ Promover │
│ en HF / manual│   │ + Cuantizar  │    │ + Checksum  │    │ a prod   │
└─────────────┘    └──────────────┘    └─────────────┘    └──────────┘
```

**Etapas del pipeline:**

1. **Trigger:** Cron semanal que verifica nuevas versiones en HF, o dispatch manual
2. **Conversión:** Ejecuta `convert_model.sh` en runner con suficiente RAM
3. **Validación:** Ejecuta `test/runner.py` contra el modelo convertido
4. **Promoción:** Si todos los tests pasan, sube el GGUF a almacenamiento (S3)
   y actualiza el Modelfile en el servidor de producción

**Archivos nuevos:** `.github/workflows/model-pipeline.yml`

---

### 8. Evaluar ventana de contexto mayor para Velvet-2B

**Problema:** `num_ctx=2048` es restrictivo para documentos legales que fácilmente
superan los 2K tokens. El test de needle-in-haystack puede pasar con corpus
sintéticos pero fallar con documentos reales.

**Impacto:** Capacidad funcional del modelo para el caso de uso objetivo.

**Recomendación:**

- Aumentar `num_ctx` a 4096 para Velvet-2B — la instancia tiene 64 GB de RAM
  y el modelo solo consume ~1.5 GB, hay margen de sobra
- Evaluar cuantización Q5_K_M para Velvet-2B si se necesita mayor calidad
  de respuesta (el costo en RAM es mínimo dado el tamaño del modelo)
- Para documentos que excedan la ventana de contexto, implementar una estrategia
  de chunking + RAG en lugar de depender del contexto raw

**Archivo afectado:** `infra/conversion/generate_modelfile.py`

```python
CONFIGURACION_MODELOS = {
    "velvet-2b": {
        "num_ctx": 4096,       # Aumentado de 2048
        "num_predict": 512,
        "temperature": 0.3,
        "top_p": 0.9,
    },
}
```

---

## Observaciones adicionales

### Archivo `velvet-key.pem` en la raíz del repositorio

Aunque `.gitignore` lo excluye del control de versiones, su presencia en el
workspace es un riesgo. Mover a `~/.ssh/velvet-key.pem` y actualizar los
scripts de conexión SSH.

### Test de stress desalineado con la configuración

El test usa 15 usuarios concurrentes pero `OLLAMA_NUM_PARALLEL=8`. Las 7
solicitudes extra se encolan, inflando artificialmente la latencia p90.
Alinear el test con la capacidad configurada o documentar explícitamente
que se está evaluando comportamiento bajo sobrecarga.

### `OLLAMA_MAX_LOADED_MODELS=1` — Documentar la restricción

Con este valor, la instancia sirve un solo modelo a la vez. Si se necesitan
Velvet-2B y Velvet-14B simultáneamente, considerar dos stacks Docker separados
o una instancia con más RAM y `MAX_LOADED_MODELS=2`.

### Health check de inferencia

El healthcheck actual (`ollama list`) solo verifica que el proceso esté vivo.
Un healthcheck más robusto ejecutaría una inferencia corta periódica:

```yaml
healthcheck:
  test: ["CMD-SHELL", "curl -sf http://localhost:11434/api/generate -d '{\"model\":\"velvet-2b-cpu-v1\",\"prompt\":\"ping\",\"stream\":false}' | grep -q 'response'"]
  interval: 60s
  timeout: 30s
  retries: 3
```

### Backup del volumen `ollama_data`

No hay estrategia de respaldo. Si el volumen Docker se corrompe, hay que
reconvertir el modelo desde cero. Considerar snapshots periódicos del
volumen EBS o exportar el modelo con `ollama push` a un registry privado.
