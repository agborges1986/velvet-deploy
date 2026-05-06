# Guía de Despliegue GPU para Benchmarks Comparativos de LLMs

## Resumen

| Parámetro | Valor |
|-----------|-------|
| Instancia | g5.2xlarge (8 vCPU, 32 GB RAM, 1x NVIDIA A10G 24 GB VRAM) |
| Región | us-east-2 (Ohio) |
| Modo | Spot (~$0.32/hr) con fallback On-Demand ($1.212/hr) |
| AMI | Deep Learning Base OSS Nvidia Driver AMI (AL2023 preferida, AL2 fallback) |
| AMI ID (us-east-2) | `ami-06ef22e45ec31551c` |
| Almacenamiento | 250 GB gp3 |
| Cuantización | Q4_K_M (misma para todos los modelos) |
| Modelos objetivo | 2B a 24B parámetros |

## Costo Estimado

| Concepto | Costo |
|----------|-------|
| Spot g5.2xlarge (~10 hrs benchmarks) | ~$3.20 |
| On-Demand g5.2xlarge (~10 hrs) | ~$12.12 |
| EBS 250 GB gp3 (proporcional) | ~$1.60 |
| AMI almacenada (250 GB, por mes) | ~$12.50/mes |
| **Total sesión (Spot)** | **~$5 USD** |
| **Total sesión (On-Demand)** | **~$14 USD** |

## Selección de AMI (orden de prioridad)

El script `ec2_provision_gpu.sh` busca AMIs en este orden:

1. **Deep Learning Base OSS Nvidia Driver AMI (Amazon Linux 2023)** — preferida (GCC 11+, drivers NVIDIA preinstalados)
2. **Deep Learning AMI GPU PyTorch (Amazon Linux 2023)** — alternativa con PyTorch incluido
3. **Deep Learning Base OSS Nvidia Driver AMI (Amazon Linux 2)** — fallback legacy (GCC 7, limitado)
4. **Amazon Linux 2023 genérica** — último recurso, requiere instalación manual de drivers con `ec2_setup_gpu.sh`

Se puede omitir la búsqueda pasando `--ami-id <AMI_ID>` directamente.

## Prerequisitos

1. AWS CLI v2 configurado con credenciales activas
2. Key Pair `velvet-key` existente en la región us-east-2
3. Archivo `velvet-key.pem` disponible localmente
4. VPC por defecto con Internet Gateway y ruta `0.0.0.0/0 → igw-xxx`

### Verificar conectividad de red (IMPORTANTE)

Antes de lanzar la instancia, verificar que la VPC por defecto tiene ruta a Internet:

```bash
# Verificar Internet Gateway
aws ec2 describe-internet-gateways --region us-east-2 \
  --filters "Name=attachment.vpc-id,Values=<VPC_ID>"

# Verificar ruta por defecto en la tabla de rutas principal
aws ec2 describe-route-tables --region us-east-2 \
  --filters "Name=vpc-id,Values=<VPC_ID>" "Name=association.main,Values=true" \
  --query "RouteTables[0].Routes"

# Debe existir una ruta: 0.0.0.0/0 → igw-xxxxxxxx
# Si no existe, agregarla:
aws ec2 create-route --region us-east-2 \
  --route-table-id <RTB_ID> \
  --destination-cidr-block 0.0.0.0/0 \
  --gateway-id <IGW_ID>
```

## Scripts Disponibles

| Script | Propósito |
|--------|-----------|
| `infra/aws/ec2_provision_gpu.sh` | Lanza instancia Spot/On-Demand g5.2xlarge |
| `infra/aws/ec2_setup_gpu.sh` | Configura drivers NVIDIA, Docker, toolkit, llama.cpp |
| `infra/aws/ec2_deploy_models.sh` | Descarga, convierte (GGUF) y despliega modelos Velvet |
| `infra/aws/ec2_create_ami.sh` | Crea AMI desde instancia configurada |
| `infra/aws/ec2_launch_from_ami.sh` | Relanza Spot desde AMI (sin repetir setup) |
| `infra/docker-compose-gpu.yml` | Docker Compose con soporte GPU |

## Flujo de Ejecución

### Primera vez (setup completo)

```bash
# 1. Lanzar instancia GPU (ahora busca AMI Amazon Linux 2023 con GCC 11+)
./infra/aws/ec2_provision_gpu.sh --key-name velvet-key

# 2. Configurar software (drivers, Docker, NVIDIA toolkit, llama.cpp)
./infra/aws/ec2_setup_gpu.sh \
  --instance-ip <IP> \
  --key-file velvet-key.pem \
  --repo-url https://github.com/agborges1986/velvet-deploy.git \
  --env-file ./infra/.env

# 3. Levantar Ollama con GPU
ssh -i velvet-key.pem ec2-user@<IP>
cd ~/velvet-project/infra
docker compose -f docker-compose-gpu.yml up -d

# 4. Descargar, convertir y desplegar modelos (todo automatizado)
./infra/aws/ec2_deploy_models.sh \
  --instance-ip <IP> \
  --key-file velvet-key.pem \
  --hf-token <TU_HF_TOKEN>

# 5. Crear AMI (captura todo el estado incluyendo modelos convertidos)
./infra/aws/ec2_create_ami.sh --instance-id <INSTANCE_ID>

# 6. Terminar instancia (ya tienes la AMI)
aws ec2 terminate-instances --region us-east-2 --instance-ids <INSTANCE_ID>
```

### Relanzar desde AMI (sesiones posteriores)

```bash
# Relanzar en ~2 minutos con todo configurado
./infra/aws/ec2_launch_from_ami.sh --ami-id <AMI_ID> --key-name velvet-key

# Conectar y arrancar directamente
ssh -i velvet-key.pem ec2-user@<IP>
cd ~/velvet-project/infra
docker compose -f docker-compose-gpu.yml up -d

# Ejecutar benchmarks
cd ~/velvet-project
python -m pytest test/ -v
```

## Recursos Creados en AWS (us-east-2)

| Recurso | ID | Estado |
|---------|-----|--------|
| **Instancia GPU** | `i-0712b07d751e3b80a` | ✅ Running (On-Demand) |
| IP Pública | `18.188.203.92` | ✅ Accesible |
| Security Group | `sg-01449ee95d11f7045` | ✅ Creado (SSH 0.0.0.0/0) |
| Key Pair | `velvet-key` | ✅ Existente |
| VPC | `vpc-0d019fc1b5edeefbf` | ✅ Default VPC |
| Subnet | `subnet-04a1c5dd4a4c785f2` | us-east-2c |
| AMI usada | `ami-06ef22e45ec31551c` | Deep Learning Base OSS Nvidia Driver (AL2023 preferida) |

### Software verificado en la instancia

| Componente | Versión | Estado |
|------------|---------|--------|
| GPU | NVIDIA A10G (23028 MiB VRAM) | ✅ |
| Driver NVIDIA | 570.211.01 | ✅ |
| Docker | 25.0.14 | ✅ |
| Docker Compose | v2.32.4 | ✅ |
| NVIDIA Container Toolkit | 1.18.2 | ✅ |
| GPU en Docker | Verificada | ✅ |
| Git | 2.47.3 | ✅ |
| huggingface-cli | 1.13.0 | ✅ |

## Problema Detectado y Resuelto: Conectividad SSH

El Security Group original solo permitía SSH desde la IP reportada por `checkip.amazonaws.com`
(162.10.24.21), pero la IP real de salida es diferente (proxy/VPN corporativa de TIVIT).

**Solución aplicada**: Se agregó `0.0.0.0/0` al puerto 22 del Security Group
`sg-01449ee95d11f7045`, igual que el SG de la instancia CPU (`sg-040739ffcce2deb4c`)
que ya funcionaba.

> ⚠️ **Nota de seguridad**: En producción, restringir SSH a IPs específicas.
> Para benchmarks temporales, `0.0.0.0/0` es aceptable si se termina la instancia al finalizar.

## Docker Compose GPU vs CPU

Diferencias clave en `docker-compose-gpu.yml`:

```yaml
# Acceso a GPU NVIDIA
deploy:
  resources:
    reservations:
      devices:
        - driver: nvidia
          count: all
          capabilities: [gpu]

# Variables optimizadas para GPU
environment:
  - OLLAMA_GPU_LAYERS=999        # Todas las capas en GPU
  - OLLAMA_FLASH_ATTENTION=1     # Flash Attention para mejor VRAM
  - OLLAMA_NUM_THREAD=4          # Menos threads CPU (GPU hace el trabajo)
```

## Limpieza de Recursos

```bash
# Terminar instancia
aws ec2 terminate-instances --region us-east-2 --instance-ids <INSTANCE_ID>

# Eliminar AMI (cuando ya no la necesites)
aws ec2 deregister-image --region us-east-2 --image-id <AMI_ID>
# También eliminar el snapshot asociado
aws ec2 describe-images --image-ids <AMI_ID> --query "Images[0].BlockDeviceMappings[0].Ebs.SnapshotId"
aws ec2 delete-snapshot --region us-east-2 --snapshot-id <SNAP_ID>

# Eliminar Security Group (opcional)
aws ec2 delete-security-group --region us-east-2 --group-id sg-01449ee95d11f7045
```
