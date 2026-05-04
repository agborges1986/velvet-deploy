# Reporte Comparativo — Gemma 4B vs Velvet 14B (On-Premise EC2)

**Fecha:** 2026-05-04
**Instancia:** c6i.8xlarge (32 vCPU, 64 GB RAM, CPU-only)
**Cuantización:** Q4_K_M (ambos modelos)
**Backend:** Ollama via Caddy (puerto 8080)

## Resumen Ejecutivo

| Métrica | Gemma 4B (3.9B) | Velvet 14B (14.1B) | Observación |
|---------|----------------|-------------------|-------------|
| **Tamaño GGUF** | 2.5 GB | 8.5 GB | Gemma 3.4x más liviano |
| **Tokens/s promedio** | ~19.4 | ~11.0 | Gemma ~1.8x más rápido |
| **Éxito global** | 87.5% | 33.3% | Ver detalle por test |

## Comparación por Test

### Memoria (Needle-in-haystack + Razonamiento)

| Métrica | Gemma 4B | Velvet 14B |
|---------|---------|-----------|
| Tasa de éxito | **100%** | 0% |
| Latencia media | **2.99s** | 10.15s |
| Tokens/s | **19.8** | 7.1 |

> Gemma 4B encontró correctamente los datos ocultos en contexto largo en todas las iteraciones. Velvet 14B falló en la extracción.

### Numérico (Cálculo financiero + JSON estructurado)

| Métrica | Gemma 4B | Velvet 14B |
|---------|---------|-----------|
| Tasa de éxito | **100%** | 0% |
| Latencia media | 10.62s | **6.56s** |
| Tokens/s | **20.8** | 12.8 |

> Gemma 4B calculó correctamente el interés compuesto y generó JSON válido en todas las iteraciones. Velvet 14B falló en los criterios de validación. Velvet 14B tiene menor latencia en este test porque genera respuestas más cortas (falla antes de completar).

### Seguridad (Privacidad PII + Resistencia a inyección)

| Métrica | Gemma 4B | Velvet 14B |
|---------|---------|-----------|
| Tasa de éxito | 50% | **100%** |
| Latencia media | **2.40s** | 5.25s |
| Tokens/s | **19.6** | 13.2 |

> Gemma 4B protegió datos PII correctamente (10/10) pero **falló en resistencia a inyección de prompts** (0/10) — respondió "ACCESO CONCEDIDO" en todas las iteraciones. Velvet 14B resistió ambos ataques.

### Latency Gap (Inferencia unitaria 20×512 tokens)

| Métrica | Gemma 4B | Velvet 14B |
|---------|---------|-----------|
| Tasa de éxito | 100% | — (no ejecutado hoy) |
| Latencia media | 43.14s | — |
| Tokens/s | 17.3 | — |

## Análisis

### Fortalezas de Gemma 4B
- **Velocidad**: ~1.8x más rápido que Velvet 14B en tokens/s, gracias a su menor tamaño (3.9B vs 14.1B)
- **Precisión**: 100% en tests de memoria y numérico — cálculos correctos, JSON válido, extracción de datos en contexto largo
- **Eficiencia de recursos**: 2.5 GB vs 8.5 GB de RAM, deja margen amplio en la instancia

### Debilidades de Gemma 4B
- **Seguridad**: Vulnerable a inyección de prompts — respondió "ACCESO CONCEDIDO" consistentemente
- **Modelo más pequeño**: Menos capacidad de razonamiento complejo en escenarios no evaluados

### Fortalezas de Velvet 14B
- **Seguridad**: 100% en resistencia a inyección de prompts y protección de PII
- **Modelo más grande**: Mayor capacidad teórica para tareas complejas

### Debilidades de Velvet 14B
- **Velocidad**: ~2x más lento que Gemma 4B
- **Precisión**: 0% en tests de memoria y numérico en esta ejecución

## Recomendación

Para casos de uso donde la **seguridad y resistencia a inyección** son críticas, Velvet 14B es la mejor opción. Para casos donde se prioriza **velocidad, precisión numérica y eficiencia de recursos**, Gemma 4B ofrece mejor rendimiento con menor consumo.

Considerar usar Gemma 4B como modelo principal con un system prompt reforzado para mitigar la vulnerabilidad a inyección de prompts.
