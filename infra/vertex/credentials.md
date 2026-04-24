# Gestión de Credenciales — Google Cloud y Vertex AI

## Índice

1. [Métodos de autenticación](#1-métodos-de-autenticación)
2. [Roles y permisos IAM necesarios](#2-roles-y-permisos-iam-necesarios)
3. [Crear y configurar una Service Account](#3-crear-y-configurar-una-service-account)
4. [Variable de entorno GOOGLE_APPLICATION_CREDENTIALS](#4-variable-de-entorno-google_application_credentials)
5. [Token de Hugging Face para modelos gated](#5-token-de-hugging-face-para-modelos-gated)
6. [Rotación de claves y buenas prácticas de seguridad](#6-rotación-de-claves-y-buenas-prácticas-de-seguridad)

---

## 1. Métodos de autenticación

Existen dos métodos principales para autenticarse con Google Cloud al usar
el SDK de Vertex AI (`google-cloud-aiplatform`).

### Método 1: `gcloud auth application-default login` (desarrollo local)

Este método es el recomendado para **desarrollo local e interactivo**. Genera
credenciales de usuario que el SDK de Python usa automáticamente.

```bash
# Paso 1: Autenticarse con tu cuenta de Google
gcloud auth login

# Paso 2: Generar Application Default Credentials (ADC)
# Estas credenciales son las que usa el SDK de Python
gcloud auth application-default login

# Paso 3: Configurar el proyecto por defecto
gcloud config set project <PROJECT_ID>
```

**Cómo funciona:**
- `gcloud auth application-default login` genera un archivo JSON de credenciales
  en `~/.config/gcloud/application_default_credentials.json`.
- El SDK de Python (`google-cloud-aiplatform`) detecta automáticamente este
  archivo y lo usa para autenticarse.
- No es necesario configurar variables de entorno adicionales.

**Cuándo usarlo:**
- Desarrollo y pruebas locales
- Ejecución manual de scripts de despliegue
- Depuración y exploración interactiva

**Limitaciones:**
- Las credenciales de usuario expiran y requieren renovación periódica.
- No es adecuado para entornos automatizados (CI/CD, cron jobs).

### Método 2: Service Account Key JSON (CI/CD y automatización)

Este método es el recomendado para **entornos automatizados** como pipelines
CI/CD, cron jobs o ejecución en servidores sin interacción humana.

```bash
# Paso 1: Crear la Service Account (ver sección 3)
# Paso 2: Generar la key JSON (ver sección 3)

# Paso 3: Configurar la variable de entorno
export GOOGLE_APPLICATION_CREDENTIALS="/ruta/a/velvet-deployer-key.json"

# Paso 4: El SDK de Python detecta automáticamente la variable
python infra/vertex/deploy_vertex.py \
  --model-id "Almawave/Velvet-2B" \
  --project "<PROJECT_ID>" \
  --region "us-central1"
```

**Cómo funciona:**
- Se crea una Service Account con los permisos mínimos necesarios.
- Se genera un archivo JSON con las credenciales de la Service Account.
- La variable `GOOGLE_APPLICATION_CREDENTIALS` apunta al archivo JSON.
- El SDK de Python detecta la variable y usa las credenciales automáticamente.

**Cuándo usarlo:**
- Pipelines CI/CD (GitHub Actions, GitLab CI, Jenkins)
- Ejecución automatizada en servidores
- Cron jobs y scripts programados
- Entornos donde no hay interacción humana

---

## 2. Roles y permisos IAM necesarios

### Opción A: Rol predefinido (recomendado para simplicidad)

El rol **Vertex AI User** (`roles/aiplatform.user`) incluye todos los permisos
necesarios para desplegar y gestionar modelos en Vertex AI.

```bash
# Asignar el rol a un usuario
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="user:<EMAIL>" \
  --role="roles/aiplatform.user"

# Asignar el rol a una Service Account
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

### Opción B: Permisos granulares (recomendado para producción)

Para seguir el principio de mínimo privilegio, se pueden asignar solo los
permisos estrictamente necesarios mediante un rol personalizado:

| Permiso                          | Descripción                                    |
|----------------------------------|------------------------------------------------|
| `aiplatform.endpoints.create`    | Crear endpoints de Vertex AI                   |
| `aiplatform.endpoints.deploy`    | Desplegar modelos en endpoints                 |
| `aiplatform.endpoints.get`       | Consultar estado de endpoints                  |
| `aiplatform.endpoints.list`      | Listar endpoints existentes                    |
| `aiplatform.endpoints.delete`    | Eliminar endpoints                             |
| `aiplatform.endpoints.predict`   | Enviar solicitudes de predicción               |
| `aiplatform.endpoints.undeploy`  | Desdesplegar modelos de endpoints              |
| `aiplatform.models.upload`       | Subir/registrar modelos                        |
| `aiplatform.models.get`          | Consultar información de modelos               |
| `aiplatform.models.list`         | Listar modelos registrados                     |
| `aiplatform.models.delete`       | Eliminar modelos del registro                  |

```bash
# Crear un rol personalizado con permisos granulares
gcloud iam roles create velvetDeployer \
  --project=<PROJECT_ID> \
  --title="Velvet Model Deployer" \
  --description="Permisos mínimos para desplegar modelos Velvet en Vertex AI" \
  --permissions="\
aiplatform.endpoints.create,\
aiplatform.endpoints.deploy,\
aiplatform.endpoints.get,\
aiplatform.endpoints.list,\
aiplatform.endpoints.delete,\
aiplatform.endpoints.predict,\
aiplatform.endpoints.undeploy,\
aiplatform.models.upload,\
aiplatform.models.get,\
aiplatform.models.list,\
aiplatform.models.delete"

# Asignar el rol personalizado
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="projects/<PROJECT_ID>/roles/velvetDeployer"
```

---

## 3. Crear y configurar una Service Account

### Paso 1: Crear la Service Account

```bash
gcloud iam service-accounts create velvet-deployer \
  --display-name="Velvet Model Deployer" \
  --description="Service Account para desplegar modelos Velvet en Vertex AI"
```

### Paso 2: Asignar roles

```bash
# Opción A: Rol predefinido
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Opción B: Rol personalizado (ver sección 2)
gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="projects/<PROJECT_ID>/roles/velvetDeployer"
```

### Paso 3: Generar la key JSON

```bash
gcloud iam service-accounts keys create velvet-deployer-key.json \
  --iam-account=velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com
```

> **Importante**: El archivo `velvet-deployer-key.json` contiene credenciales
> sensibles. Guárdalo en un lugar seguro y **nunca lo incluyas en el control
> de versiones**.

### Paso 4: Verificar la Service Account

```bash
# Listar las Service Accounts del proyecto
gcloud iam service-accounts list --project=<PROJECT_ID>

# Verificar los roles asignados
gcloud projects get-iam-policy <PROJECT_ID> \
  --flatten="bindings[].members" \
  --filter="bindings.members:velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com" \
  --format="table(bindings.role)"

# Listar las keys de la Service Account
gcloud iam service-accounts keys list \
  --iam-account=velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com
```

---

## 4. Variable de entorno GOOGLE_APPLICATION_CREDENTIALS

La variable `GOOGLE_APPLICATION_CREDENTIALS` indica al SDK de Google Cloud
dónde encontrar el archivo JSON de credenciales de la Service Account.

### Configuración temporal (sesión actual)

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/ruta/absoluta/a/velvet-deployer-key.json"
```

### Configuración persistente (bash/zsh)

```bash
# Agregar al archivo de perfil del shell
echo 'export GOOGLE_APPLICATION_CREDENTIALS="/ruta/absoluta/a/velvet-deployer-key.json"' >> ~/.bashrc
source ~/.bashrc

# O para zsh:
echo 'export GOOGLE_APPLICATION_CREDENTIALS="/ruta/absoluta/a/velvet-deployer-key.json"' >> ~/.zshrc
source ~/.zshrc
```

### Configuración en CI/CD

**GitHub Actions:**
```yaml
env:
  GOOGLE_APPLICATION_CREDENTIALS: ${{ secrets.GCP_SA_KEY_PATH }}
# O usar el contenido directamente:
steps:
  - name: Configurar credenciales GCP
    run: |
      echo '${{ secrets.GCP_SA_KEY }}' > /tmp/gcp-key.json
      echo "GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-key.json" >> $GITHUB_ENV
```

**GitLab CI:**
```yaml
variables:
  GOOGLE_APPLICATION_CREDENTIALS: /tmp/gcp-key.json

before_script:
  - echo "$GCP_SA_KEY" > $GOOGLE_APPLICATION_CREDENTIALS
```

### Verificar la configuración

```bash
# Verificar que la variable está configurada
echo $GOOGLE_APPLICATION_CREDENTIALS

# Verificar que el archivo existe y es legible
cat "$GOOGLE_APPLICATION_CREDENTIALS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Service Account: {d[\"client_email\"]}')"

# Verificar autenticación con el SDK
python3 -c "
from google.cloud import aiplatform
aiplatform.init(project='<PROJECT_ID>', location='us-central1')
print('Autenticación exitosa')
"
```

---

## 5. Token de Hugging Face para modelos gated

Si los modelos de Almawave (Velvet-2B, Velvet-14B) son modelos gated en
Hugging Face, es necesario configurar un token de acceso para que Vertex AI
pueda descargarlos durante el despliegue.

### Obtener el token de Hugging Face

1. Ir a [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Crear un nuevo token con permisos de lectura (`read`)
3. Copiar el token generado (formato: `hf_xxxxxxxxxxxxxxxxxxxx`)

### Solicitar acceso a modelos gated

Si los modelos requieren aprobación:

1. Ir a la página del modelo (ej: `https://huggingface.co/Almawave/Velvet-2B`)
2. Hacer clic en "Request access" o "Agree to terms"
3. Esperar la aprobación del propietario del modelo

### Configurar el token para Vertex AI

Cuando se despliega un modelo gated en Vertex AI usando la integración nativa
de Hugging Face, el token se puede configurar de las siguientes formas:

**Opción 1: Variable de entorno (recomendado)**

```bash
# Configurar antes de ejecutar el script de despliegue
export HF_TOKEN="hf_xxxxxxxxxxxxxxxxxxxx"

python infra/vertex/deploy_vertex.py \
  --model-id "Almawave/Velvet-2B" \
  --project "<PROJECT_ID>" \
  --region "us-central1"
```

**Opción 2: Configuración con huggingface-cli**

```bash
# Configurar el token globalmente
huggingface-cli login
# Pegar el token cuando se solicite

# Verificar el acceso
huggingface-cli whoami
huggingface-cli repo info Almawave/Velvet-2B
```

### Verificar el acceso al modelo

```bash
# Verificar que el token es válido
huggingface-cli whoami

# Verificar acceso a un modelo específico
huggingface-cli repo info Almawave/Velvet-2B

# Intentar descargar un archivo pequeño del modelo
huggingface-cli download Almawave/Velvet-2B config.json --local-dir /tmp/test-hf
```

---

## 6. Rotación de claves y buenas prácticas de seguridad

### Rotación de Service Account Keys

Las keys de Service Account **deben rotarse periódicamente** (recomendado:
cada 90 días como máximo).

```bash
# Paso 1: Generar una nueva key
gcloud iam service-accounts keys create velvet-deployer-key-nueva.json \
  --iam-account=velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com

# Paso 2: Actualizar la variable de entorno o el secreto en CI/CD
export GOOGLE_APPLICATION_CREDENTIALS="/ruta/a/velvet-deployer-key-nueva.json"

# Paso 3: Verificar que la nueva key funciona
python3 -c "
from google.cloud import aiplatform
aiplatform.init(project='<PROJECT_ID>', location='us-central1')
print('Nueva key verificada correctamente')
"

# Paso 4: Listar las keys existentes
gcloud iam service-accounts keys list \
  --iam-account=velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com

# Paso 5: Eliminar la key antigua (usar el KEY_ID de la lista anterior)
gcloud iam service-accounts keys delete <KEY_ID_ANTIGUA> \
  --iam-account=velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com

# Paso 6: Eliminar el archivo de la key antigua de forma segura
shred -u velvet-deployer-key-antigua.json  # Linux
# o: rm -P velvet-deployer-key-antigua.json  # macOS
```

### Buenas prácticas de seguridad

#### Principio de mínimo privilegio
- Usar permisos granulares en lugar del rol amplio `roles/aiplatform.admin`.
- Preferir `roles/aiplatform.user` sobre `roles/aiplatform.admin`.
- Crear roles personalizados con solo los permisos necesarios (ver sección 2).

#### Protección de archivos de credenciales
- **Nunca incluir archivos de credenciales en el control de versiones.**
  Agregar al `.gitignore`:
  ```
  # Credenciales de Google Cloud
  *-key.json
  *-credentials.json
  application_default_credentials.json
  ```
- Proteger los archivos con permisos restrictivos:
  ```bash
  chmod 600 velvet-deployer-key.json
  ```
- Almacenar credenciales en gestores de secretos (Google Secret Manager,
  AWS Secrets Manager, HashiCorp Vault) en lugar de archivos locales.

#### Auditoría y monitoreo
- Habilitar **Cloud Audit Logs** para registrar todas las operaciones de
  Vertex AI.
- Configurar alertas para operaciones sensibles (creación de endpoints,
  despliegue de modelos).
- Revisar periódicamente los accesos con **IAM Recommender** para identificar
  permisos no utilizados.

#### Gestión de Service Accounts
- Crear una Service Account dedicada por proyecto/entorno (no reutilizar
  entre proyectos).
- Deshabilitar Service Accounts no utilizadas en lugar de eliminarlas
  (permite reactivación si es necesario):
  ```bash
  gcloud iam service-accounts disable \
    velvet-deployer@<PROJECT_ID>.iam.gserviceaccount.com
  ```
- Limitar el número de keys por Service Account (máximo 2: una activa y
  una en proceso de rotación).

#### Token de Hugging Face
- Crear tokens con permisos mínimos: solo lectura (`read`) si solo se
  necesita descargar modelos.
- Rotar el token si se sospecha compromiso: revocar en
  [https://huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
  y generar uno nuevo.
- No compartir el token entre equipos. Cada miembro o Service Account debe
  tener su propio token.
- En CI/CD, almacenar el token como secreto del pipeline, no como variable
  de entorno en texto plano en archivos de configuración.

#### Seguridad de red
- Restringir el acceso a los endpoints de Vertex AI usando VPC Service
  Controls si los datos son sensibles.
- Usar Private Service Connect para acceder a Vertex AI desde redes
  privadas sin pasar por internet público.
