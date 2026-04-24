# Informe de Optimización — Velvet 14B en EC2 c6i.8xlarge

**Fecha:** 24 de April de 2026
**Hardware:** EC2 c6i.8xlarge (32 vCPU / 16 cores físicos, 64 GB RAM)
**Modelo:** velvet-14b-cpu-v1 (Q4_K_M, 8.5 GB)
**Engine:** Ollama 0.21.2

---

## Matriz de Tests Ejecutados

| Test | NUM_PARALLEL | NUM_THREAD | Usuarios | num_ctx | Descripción |
|------|-------------|------------|----------|---------|-------------|
| T1 | 1 | 32 | 1 | 4096 | Baseline: 1 usuario, todos los cores |
| T2 | 2 | 16 | 2 | 4096 | 2 paralelas x 16 threads |
| T3 | 4 | 8 | 4 | 4096 | Config actual (referencia) |
| T4 | 8 | 4 | 8 | 4096 | Alta concurrencia |
| T5 | 4 | 8 | 8 | 4096 | Sobrecarga 2x sobre config actual |
| T6 | 4 | 8 | 4 | 2048 | Contexto reducido |
| T7 | 4 | 8 | 4 | 8192 | Contexto amplio |

---

## Resultados

| Test | Throughput (T/s) | Latencia Media | TTFT | Lat p90 | TTFT p90 | TPS/usuario | Tokens |
|------|-----------------|----------------|------|---------|----------|-------------|--------|
| T1 | **10.0** | 78.53s | 6.747s | 78.53s | 6.747s | 10.0 | 786 |
| T2 | **12.7** | 70.88s | 7.579s | 85.8s | 8.235s | 7.5 | 1087 |
| T3 | **16.9** | 113.66s | 9.898s | 128.41s | 10.722s | 4.8 | 2171 |
| T4 | **18.1** | 197.45s | 14.943s | 270.05s | 15.835s | 3.0 | 4877 |
| T5 | **18.0** | 153.08s | 56.625s | 241.76s | 137.915s | 3.8 | 4345 |
| T6 | **14.7** | 107.06s | 8.641s | 150.26s | 9.431s | 5.0 | 2209 |
| T7 | **17.7** | 98.46s | 10.381s | 105.43s | 11.182s | 4.7 | 1863 |

---

## Configuración Óptima por Métrica

| Métrica | Mejor Test | Valor | Configuración |
|---------|-----------|-------|---------------|
| Mayor Throughput (T/s) | **T4** | **18.1** | P=8, T=4, U=8, ctx=4096 |
| Menor TTFT | **T1** | **6.747s** | P=1, T=32, U=1, ctx=4096 |
| Menor Latencia Media | **T2** | **70.88s** | P=2, T=16, U=2, ctx=4096 |
| Mayor TPS por Usuario | **T1** | **10.0** | P=1, T=32, U=1, ctx=4096 |

---

## Análisis

### Baseline (T1) vs Config Actual (T3)

- T1 (1 usuario, 32 threads): 10.0 TPS/usuario, TTFT 6.747s
- T3 (4 usuarios, 8 threads): 4.8 TPS/usuario, TTFT 9.898s
- Throughput agregado: T1=10.0 T/s vs T3=16.9 T/s
- La concurrencia de T3 genera **1.7x** más throughput agregado

### Config Actual (T3) vs Alta Concurrencia (T4)

- T3 (P=4, T=8, 4 users): Throughput 16.9 T/s, TTFT 9.898s
- T4 (P=8, T=4, 8 users): Throughput 18.1 T/s, TTFT 14.943s
- Alta concurrencia mejora throughput en 7%

### Impacto de Ventana de Contexto (T6 vs T3 vs T7)

- T6 (ctx=2048): Throughput 14.7 T/s, TTFT 8.641s
- T3 (ctx=4096): Throughput 16.9 T/s, TTFT 9.898s
- T7 (ctx=8192): Throughput 17.7 T/s, TTFT 10.381s

---

## Recomendación

La configuración óptima para Velvet-14B en EC2 c6i.8xlarge es **T4** 
si se prioriza throughput agregado, o **T1** si se prioriza 
rendimiento por usuario individual.
