# Gestión de Credenciales — AWS y On-Premise

## Índice

1. [Credenciales AWS (IAM)](#1-credenciales-aws-iam)
2. [Configuración de AWS CLI](#2-configuración-de-aws-cli)
3. [Credenciales de Caddy (Reverse Proxy)](#3-credenciales-de-caddy-reverse-proxy)
4. [Token de Hugging Face (HF_TOKEN)](#4-token-de-hugging-face-hf_token)
5. [Flujo completo de despliegue](#5-flujo-completo-de-despliegue)
6. [Rotación de credenciales y buenas prácticas](#6-rotación-de-credenciales-y-buenas-prácticas)

---

## 1. Credenciales AWS (IAM)

### IAM User o IAM Role necesario

Para ejecutar el script de aprovisionamiento (`ec2_provision.sh`), se necesita un
IAM User o IAM Role con los siguientes permisos mínimos:

| Permiso                                  | Descripción                                      |
|------------------------------------------|--------------------------------------------------|
| `ec2:RunInstances`                       | Lanzar instancias EC2                            |
| `ec2:CreateSecurityGroup`                | Crear Security Groups                            |
| `ec2:AuthorizeSecurityGroupIngress`      | Agregar reglas de entrada al Security Group      |
| `ec2:CreateKeyPair`                      | Crear Key Pairs para acceso SSH                  |
| `ec2:DescribeInstances`                  | Consultar estado e IPs de instancias             |
| `ec2:CreateTags`                         | Etiquetar recursos (instancias, Security Groups) |
| `ec2:DescribeImages`                     | Buscar AMIs de Amazon Linux 2023                 |
| `ec2:DescribeKeyPairs`                   | Verificar si un Key Pair ya existe               |
| `ec2:DescribeVpcs`                       | Obtener la VPC por defecto                       |
| `ec2:TerminateInstances`                 | Terminar instancias (para teardown)              |
| `ec2:DeleteSecurityGroup`                | Eliminar Security Groups (para teardown)         |
| `ec2:DeleteKeyPair`                      | Eliminar Key Pairs (para teardown)               |
| `ec2:DescribeSecurityGroups`             | Consultar Security Groups existentes             |

### Crear un IAM User dedicado

```bash
# Crear el usuario
aws iam create-user --user-name velvet-deployer

# Crear una política personalizada con los permisos mínimos
cat > velvet-ec2-policy.json << 'EOF'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "VelvetEC2Provisioning",
            "Effect": "Allow",
            "Action": [
                "ec2:RunInstances",
                "ec2:CreateSecurityGroup",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateKeyPair",
                "ec2:DescribeInstances",
                "ec2:CreateTags",
                "ec2:DescribeImages",
                "ec2:DescribeKeyPairs",
                "ec2:DescribeVpcs",
                "ec2:TerminateInstances",
                "ec2:DeleteSecurityGroup",
                "ec2:DeleteKeyPair",
                "ec2:DescribeSecurityGroups"
            ],
            "Resource": "*"
        }
    ]
}
EOF

# Adjuntar la política al usuario
aws iam put-user-policy \
    --user-name velvet-deployer \
    --policy-name VelvetEC2Policy \
    --policy-document file://velvet-ec2-policy.json

# Generar credenciales de acceso
aws iam create-access-key --user-name velvet-deployer
```

> **Nota**: Guarda el `AccessKeyId` y `SecretAccessKey` en un lugar seguro.
> No se pueden recuperar después de la creación.

---

## 2. Configuración de AWS CLI

### Opción A: Configuración interactiva con `aws configure`

```bash
aws configure
# AWS Access Key ID [None]: <TU_ACCESS_KEY_ID>
# AWS Secret Access Key [None]: <TU_SECRET_ACCESS_KEY>
# Default region name [None]: us-east-1
# Default output format [None]: json
```

Las credenciales se guardan en `~/.aws/credentials` y la configuración en
`~/.aws/config`.

### Opción B: Variables de entorno

Útil para CI/CD o ejecución automatizada:

```bash
export AWS_ACCESS_KEY_ID="<TU_ACCESS_KEY_ID>"
export AWS_SECRET_ACCESS_KEY="<TU_SECRET_ACCESS_KEY>"
export AWS_DEFAULT_REGION="us-east-1"
```

### Opción C: Perfil con nombre

Para manejar múltiples cuentas AWS:

```bash
aws configure --profile velvet
# Completar las credenciales...

# Usar el perfil en los scripts:
export AWS_PROFILE=velvet
```

### Verificar la configuración

```bash
# Verificar identidad
aws sts get-caller-identity

# Verificar permisos EC2
aws ec2 describe-instances --region us-east-1 --max-results 1
```

---

## 3. Credenciales de Caddy (Reverse Proxy)

El reverse proxy Caddy protege la API de Ollama con autenticación Basic Auth.
Se necesitan dos valores en el archivo `.env`:

### Generar el hash de contraseña

Caddy requiere un hash bcrypt de la contraseña. Para generarlo:

```bash
# Opción 1: Usando Caddy directamente (si está instalado localmente)
caddy hash-password --plaintext "mi_contraseña_segura"
# Salida: $2a$14$Zkx19XLiW6VYouLRR3bKz.....

# Opción 2: Usando Docker (sin instalar Caddy localmente)
docker run --rm caddy:2-alpine caddy hash-password --plaintext "mi_contraseña_segura"

# Opción 3: Usando htpasswd (disponible en apache2-utils)
htpasswd -nbBC 14 "" "mi_contraseña_segura" | cut -d: -f2
```

### Configurar en el archivo `.env`

```dotenv
OLLAMA_AUTH_USER=admin
OLLAMA_AUTH_HASH=$2a$14$Zkx19XLiW6VYouLRR3bKz.....
```

> **Importante**: El hash completo (incluyendo el prefijo `$2a$`) debe copiarse
> sin modificaciones. No uses la contraseña en texto plano.

### Verificar la autenticación

Una vez desplegado, verificar que la autenticación funciona:

```bash
# Sin credenciales → debe retornar 401
curl -s -o /dev/null -w "%{http_code}" http://<IP>:8080/api/tags
# Esperado: 401

# Con credenciales → debe retornar 200
curl -s -o /dev/null -w "%{http_code}" -u admin:mi_contraseña_segura http://<IP>:8080/api/tags
# Esperado: 200
```

---

## 4. Token de Hugging Face (HF_TOKEN)

El token de Hugging Face es necesario para descargar modelos privados o con
acceso restringido (gated models) como los de Almawave.

### Obtener el token

1. Ir a [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Crear un nuevo token con permisos de lectura (`read`)
3. Copiar el token generado (formato: `hf_xxxxxxxxxxxxxxxxxxxx`)

### Solicitar acceso a modelos gated

Si los modelos de Almawave son gated:

1. Ir a la página del modelo (ej: `https://huggingface.co/Almawave/Velvet-2B`)
2. Hacer clic en "Request access" o "Agree to terms"
3. Esperar la aprobación del propietario del modelo

### Configurar el token

```bash
# Opción 1: En el archivo .env del proyecto
echo "HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx" >> infra/.env

# Opción 2: Como variable de entorno del sistema
export HF_TOKEN="hf_xxxxxxxxxxxxxxxxxxxx"

# Opción 3: Usando huggingface-cli (configura ~/.huggingface/token)
huggingface-cli login
# Pegar el token cuando se solicite
```

### Verificar el acceso

```bash
# Verificar que el token es válido
huggingface-cli whoami

# Verificar acceso a un modelo específico
huggingface-cli repo info Almawave/Velvet-2B
```

---

## 5. Flujo completo de despliegue

El siguiente diagrama muestra el flujo completo desde la configuración de
credenciales hasta el despliegue operativo:

```
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 1: Configurar credenciales AWS                                │
│                                                                     │
│   aws configure                                                     │
│   # o exportar AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY             │
│   aws sts get-caller-identity   # verificar                        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 2: Lanzar instancia EC2                                       │
│                                                                     │
│   ./infra/aws/ec2_provision.sh --key-name velvet-key                │
│   # Anota: Instance ID, IP pública, Security Group ID               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 3: Preparar archivo .env con credenciales on-premise          │
│                                                                     │
│   cp infra/.env.example infra/.env                                  │
│   # Editar infra/.env:                                              │
│   #   OLLAMA_AUTH_USER=admin                                        │
│   #   OLLAMA_AUTH_HASH=<hash generado con caddy hash-password>      │
│   #   HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx                              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 4: Configurar la instancia EC2 (SSH + Docker + dependencias)  │
│                                                                     │
│   ./infra/aws/ec2_setup.sh \                                        │
│     --instance-ip <IP_PUBLICA> \                                    │
│     --key-file velvet-key.pem \                                     │
│     --repo-url https://github.com/org/velvet-project.git \          │
│     --env-file ./infra/.env                                         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 5: Desplegar con Docker Compose                               │
│                                                                     │
│   ssh -i velvet-key.pem ec2-user@<IP_PUBLICA>                       │
│   cd ~/velvet-project/infra                                         │
│   docker compose up -d                                              │
│   docker compose logs -f   # verificar que todo arranca             │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 6: Verificar el despliegue                                    │
│                                                                     │
│   # Desde la máquina local:                                         │
│   curl -u admin:<contraseña> http://<IP_PUBLICA>:8080/api/tags      │
│   # Debe retornar la lista de modelos disponibles                   │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 7: Ejecutar pruebas                                           │
│                                                                     │
│   python -m test.runner \                                           │
│     --backend ollama \                                              │
│     --model velvet-2b-cpu-v1 \                                      │
│     --output-dir ./results/                                         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│ PASO 8: Limpieza de recursos (cuando ya no se necesiten)           │
│                                                                     │
│   ./infra/aws/teardown.sh \                                         │
│     --instance-id <INSTANCE_ID> \                                   │
│     --security-group-id <SG_ID> \                                   │
│     --key-name velvet-key \                                         │
│     --confirm                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. Rotación de credenciales y buenas prácticas

### Credenciales AWS

- **Rotar las Access Keys cada 90 días** como máximo.
- Usar `aws iam create-access-key` para generar nuevas claves y
  `aws iam delete-access-key` para eliminar las antiguas.
- **Preferir IAM Roles sobre IAM Users** cuando sea posible (ej: si se ejecuta
  desde otra instancia EC2 o desde un pipeline CI/CD).
- **Nunca incluir credenciales AWS en el código fuente** ni en archivos
  versionados. Usar variables de entorno o `aws configure`.
- Habilitar **MFA (Multi-Factor Authentication)** en la cuenta IAM.
- Revisar periódicamente los permisos con **IAM Access Analyzer**.

### Credenciales de Caddy (Basic Auth)

- **Cambiar la contraseña de Caddy periódicamente** (mínimo cada 90 días).
- Para rotar:
  1. Generar un nuevo hash: `caddy hash-password --plaintext "nueva_contraseña"`
  2. Actualizar `OLLAMA_AUTH_HASH` en el archivo `.env`
  3. Reiniciar Caddy: `docker compose restart caddy`
- **Usar contraseñas fuertes**: mínimo 16 caracteres, combinando mayúsculas,
  minúsculas, números y símbolos.
- **No reutilizar contraseñas** de otros servicios.

### Token de Hugging Face

- **Crear tokens con permisos mínimos**: solo lectura (`read`) si solo se
  necesita descargar modelos.
- **Rotar el token si se sospecha compromiso**: revocar en
  [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
  y generar uno nuevo.
- **No compartir el token** entre equipos. Cada miembro debe tener su propio
  token.

### Key Pairs SSH

- **Proteger el archivo .pem** con permisos restrictivos: `chmod 400 <archivo>.pem`
- **No compartir archivos .pem** por canales inseguros (email, chat).
- **Eliminar Key Pairs no utilizados** con `aws ec2 delete-key-pair`.
- Considerar usar **AWS Systems Manager Session Manager** como alternativa a
  SSH directo para mayor seguridad y auditoría.

### Buenas prácticas generales

- **Principio de mínimo privilegio**: otorgar solo los permisos estrictamente
  necesarios para cada operación.
- **Auditoría**: habilitar AWS CloudTrail para registrar todas las llamadas API.
- **Archivos .env**: incluir en `.gitignore` y nunca versionar. Usar
  `.env.example` como plantilla documentada.
- **Secretos en CI/CD**: usar el gestor de secretos del proveedor (ej: GitHub
  Secrets, AWS Secrets Manager) en lugar de variables de entorno en texto plano.
- **Limpieza**: ejecutar `teardown.sh` cuando los recursos ya no sean necesarios
  para evitar costos innecesarios y reducir la superficie de ataque.
