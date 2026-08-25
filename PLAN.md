# Plan de MVP: AnberPod para RG35XX H / MuOS

## 1. Objetivo, límites y definición de terminado

Construir por fases una aplicación nativa de podcasts que se ejecute desde `Roms/APPS` en una Anbernic RG35XX H con MuOS, con interfaz PySDL2 a 640×480, control exclusivo por botones físicos y estado durable en la tarjeta SD. El MVP permite explorar y buscar en Podcast Index, importar RSS públicos, suscribirse localmente, actualizar bajo demanda, reproducir por HTTPS, reanudar, descargar y reproducir sin red.

El MVP termina únicamente cuando pasan las pruebas automatizadas de host y la lista de validación en hardware de la sección 13. Cada fase debe dejar un incremento ejecutable y verificable; ninguna fase posterior debe ser necesaria para probar la anterior.

Queda expresamente fuera del MVP: cuentas, sincronización, analítica o telemetría, recomendaciones personalizadas, feeds privados o autenticados, descargas automáticas, eliminación automática, colas, velocidad variable, teclado en la consola y cualquier reproductor multimedia aportado por el firmware. El repositorio y los artefactos publicados no contendrán credenciales, feeds privados ni episodios descargados.

## 2. Decisiones de alcance y arquitectura

- Un solo proceso Python 3.10 presenta la interfaz y coordina servicios. El trabajo bloqueante de red, descarga, lectura de feed y control de procesos corre fuera del hilo SDL y entrega eventos a una cola acotada.
- SQLite es la fuente de verdad local para catálogo visto, suscripciones, episodios, progreso y estado de descargas. Los archivos grandes viven fuera de SQLite. La base usa claves foráneas, transacciones, WAL y migraciones crecientes.
- El directorio de datos es independiente del código versionado. El lanzador pasa una ruta absoluta `ANBERPOD_DATA_DIR`; actualizar una versión nunca copia, limpia ni reemplaza ese directorio.
- Podcast Index sirve exclusivamente para categorías, búsqueda y metadatos de descubrimiento. Una suscripción conserva la URL canónica del feed y se actualiza leyendo RSS bajo demanda, de modo que sigue siendo útil sin el catálogo.
- El audio remoto y local se decodifica con un `ffmpeg` ARM64 estático, de ruta configurable, a PCM firmado little-endian de 16 bits, 48 kHz y dos canales. El PCM se entrega a ALSA mediante `aplay`; no se invoca mpv, VLC ni otro reproductor del firmware.
- La reproducción local tiene prioridad si existe una descarga marcada `complete` cuyo tamaño y archivo coinciden. En otro caso se usa la URL HTTPS remota. No se reproduce un archivo `.part`.
- La primera versión soporta RSS 2.0 y Atom públicos, con namespaces habituales de podcast (iTunes y Podcast Namespace) sólo en los campos que necesita el producto. HTML, OPML y feeds autenticados quedan fuera.
- Se muestra siempre el estado local válido primero. La falta de red produce un aviso no modal y no impide entrar en Suscripciones, Descargas, Ajustes o reproducir archivos completos.

## 3. Disposición prevista del repositorio

La implementación futura seguirá esta estructura; este documento no crea todavía ninguno de estos módulos:

```text
anberpod/
├── pyproject.toml
├── requirements.lock
├── requirements-dev.lock
├── src/anberpod/
│   ├── __main__.py                 # composición y arranque
│   ├── app.py                      # bucle de aplicación y coordinación
│   ├── domain/
│   │   ├── models.py               # entidades y enums sin SDL/red/SQLite
│   │   ├── ports.py                # Protocols mockables
│   │   └── errors.py               # errores tipados para UI/log
│   ├── services/
│   │   ├── discovery.py            # casos de uso Podcast Index
│   │   ├── feeds.py                # importar/actualizar/suscribir
│   │   ├── downloads.py            # máquina de estados de descarga
│   │   └── playback.py             # selección local/remota y progreso
│   ├── adapters/
│   │   ├── podcast_index.py
│   │   ├── http.py                 # transporte HTTPS y política común
│   │   ├── rss.py                  # parseo XML limitado
│   │   ├── sqlite.py               # repositorios y migraciones
│   │   ├── filesystem.py           # escritura, fsync y reemplazo atómico
│   │   ├── ffmpeg_aplay.py
│   │   └── sdl_input.py
│   ├── ui/
│   │   ├── state.py                # rutas, foco y view-models
│   │   ├── screens.py              # pantallas 640×480
│   │   ├── widgets.py              # listas, diálogo y teclado virtual
│   │   ├── renderer.py             # PySDL2/Pillow
│   │   └── assets/                 # fuentes e imágenes redistribuibles
│   └── migrations/
│       ├── 001_initial.sql
│       └── ...
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── fixtures/                   # RSS/XML/audio mínimos, todos públicos/sintéticos
│   └── hardware/                   # guion/manual y recolector de diagnóstico
├── packaging/muos/
│   ├── AnberPod.sh
│   ├── README-INSTALL.txt
│   └── config.example.toml
├── scripts/
│   ├── build_bundle.sh
│   ├── check_bundle.sh
│   └── deploy_sd.sh
└── docs/
    ├── RSS-IMPORT.md
    └── HARDWARE-VALIDATION.md
```

`requirements.lock` fijará versiones compatibles con Python 3.10, incluidas PySDL2, Pillow y un parser XML endurecido; no se aceptarán dependencias que requieran compilar en la consola. Las pruebas del dominio no importarán SDL, abrirán red ni ejecutarán audio real.

## 4. Contratos mockables

Los puertos se expresarán como `typing.Protocol` y usarán modelos del dominio, no objetos de bibliotecas externas:

- `Clock.now_utc() -> datetime` y `MonotonicClock.seconds() -> float`: fechas, expiración de caché y progreso deterministas.
- `PodcastCatalog.categories()`, `search(query, limit)` y `podcast(feed_id)`: catálogo desacoplado de Podcast Index.
- `HttpTransport.request(RequestPolicy, url, headers) -> HttpResponse`: única frontera HTTP; admite un transporte falso con secuencias de respuestas y redirecciones.
- `FeedReader.fetch(url, validators) -> FeedFetchResult` y `parse(bytes, source_url) -> ParsedFeed`: validación HTTP separada del parseo XML.
- `PodcastRepository`, `EpisodeRepository`, `PlaybackRepository`, `DownloadRepository` y `SettingsRepository`: operaciones transaccionales con dobles en memoria.
- `AtomicFiles.commit_temp(temp, destination)`, `exists`, `size`, `unlink`: sistema de archivos sustituible; `unlink` sólo recibe rutas ya resueltas dentro del directorio de datos.
- `DownloadRunner.start(job)`, `cancel(id)` y `events()`: descarga por bloques y eventos de avance sin acoplar la UI a hilos.
- `PlaybackEngine.play(source, start_seconds)`, `pause`, `resume`, `seek_relative`, `stop`, `events()` y `shutdown`: proceso ffmpeg/aplay sustituible por un motor determinista.
- `InputSource.poll() -> list[InputEvent]`: SDL aislado de la navegación.
- `ConnectivityProbe.is_online()`: pista para UI; nunca reemplaza el manejo del error real.
- `CredentialProvider.podcast_index()`: lee secretos sólo de configuración local y permite un falso en tests.
- `Logger`: registra mensajes estructurados sin secretos, query strings sensibles ni cabeceras.

Los casos de uso recibirán estos puertos por constructor. Los tests de contrato ejecutarán la misma batería contra repositorios SQLite y sus dobles para evitar que el mock tenga semántica distinta.

## 5. Datos locales, esquema y durabilidad

Ruta estable propuesta en la SD:

```text
Roms/APPS/AnberPod/
├── current -> releases/<versión>/       # o copia seleccionada por el instalador
├── releases/<versión>/                  # código reemplazable
├── runtime/bin/ffmpeg                    # ARM64 estático, reemplazable
└── data/                                 # nunca incluido ni borrado por una actualización
    ├── db/anberpod.sqlite3
    ├── downloads/<episode_uuid>.<ext>
    ├── cache/images/
    ├── cache/http/
    ├── imports/rss_urls.txt
    ├── imports/rss_urls.result.txt
    ├── config/config.toml
    └── logs/anberpod.log
```

Si MuOS o el sistema de archivos de la SD no soporta enlaces simbólicos, `current/` será un directorio reemplazable; el lanzador seguirá resolviendo `data/` como hermano, nunca como hijo. El instalador crea datos ausentes, pero aborta antes de tocar datos existentes. Los logs rotan localmente con un máximo documentado (por defecto, 3 archivos de 1 MiB).

Esquema inicial exacto, con tiempos UTC en texto RFC 3339 y duraciones/posiciones como milisegundos enteros:

| Tabla | Columnas y restricciones relevantes |
|---|---|
| `schema_migration` | `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL` |
| `podcast` | `id TEXT PRIMARY KEY` (UUID local), `feed_url TEXT NOT NULL UNIQUE`, `catalog_id INTEGER NULL`, `title TEXT NOT NULL`, `author TEXT`, `description TEXT`, `image_url TEXT`, `language TEXT`, `etag TEXT`, `last_modified TEXT`, `last_checked_at TEXT`, `last_success_at TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL` |
| `subscription` | `podcast_id TEXT PRIMARY KEY REFERENCES podcast(id) ON DELETE CASCADE`, `subscribed_at TEXT NOT NULL` |
| `episode` | `id TEXT PRIMARY KEY` (UUID local estable), `podcast_id TEXT NOT NULL REFERENCES podcast(id) ON DELETE CASCADE`, `source_key TEXT NOT NULL`, `guid TEXT`, `media_url TEXT NOT NULL`, `title TEXT NOT NULL`, `description TEXT`, `published_at TEXT`, `duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0)`, `media_length_bytes INTEGER CHECK(media_length_bytes IS NULL OR media_length_bytes >= 0)`, `media_type TEXT`, `image_url TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `UNIQUE(podcast_id, source_key)` |
| `playback` | `episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE`, `position_ms INTEGER NOT NULL DEFAULT 0 CHECK(position_ms >= 0)`, `duration_ms INTEGER`, `completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1))`, `updated_at TEXT NOT NULL` |
| `download` | `episode_id TEXT PRIMARY KEY REFERENCES episode(id) ON DELETE CASCADE`, `state TEXT NOT NULL CHECK(state IN ('queued','downloading','complete','failed'))`, `relative_path TEXT`, `temp_relative_path TEXT`, `bytes_received INTEGER NOT NULL DEFAULT 0 CHECK(bytes_received >= 0)`, `bytes_total INTEGER`, `etag TEXT`, `last_modified TEXT`, `error_code TEXT`, `created_at TEXT NOT NULL`, `updated_at TEXT NOT NULL`, `completed_at TEXT`, con checks que exijan ruta final sólo para `complete` |
| `catalog_cache` | `cache_key TEXT PRIMARY KEY`, `payload_relative_path TEXT NOT NULL`, `fetched_at TEXT NOT NULL`, `expires_at TEXT NOT NULL`, `etag TEXT`, `last_modified TEXT` |
| `setting` | `key TEXT PRIMARY KEY`, `value TEXT NOT NULL`, limitada por repositorio a claves conocidas y no secretas |

`source_key` se deriva en orden de `guid` no vacío, URL normalizada del enclosure o, como último recurso, hash de título+fecha+URL; así una actualización hace upsert y no duplica episodios. La baja de una suscripción elimina sólo `subscription`: no elimina podcast, episodios, progreso ni descargas. El borrado manual de una descarga elimina su archivo y fila de `download`, no suscripción, episodio ni `playback`.

Cada migración corre en una transacción y crea primero una copia de seguridad limitada de la base. Si falla, la app conserva la base previa, registra el error y no inicia en modo escritura. Cachés y descargas se escriben a un archivo temporal en el mismo directorio, se vacían con `fsync`, se validan y se publican con reemplazo atómico. Al arrancar, los `.part` quedan como descarga fallida/reanudable; jamás se presentan como completos. La caché puede descartarse si está corrupta, pero la aplicación mantiene el último registro local válido.

## 6. Reglas seguras para Podcast Index, HTTP y RSS

### Credenciales de Podcast Index

- `data/config/config.toml`, fuera del repositorio y con permisos `0600` cuando el sistema lo permita, contiene `api_key` y `api_secret`; el paquete incluye sólo nombres de campos de ejemplo vacíos.
- Por cada petición se genera `X-Auth-Date` desde `Clock`, y `Authorization = SHA1(api_key + api_secret + X-Auth-Date)` conforme al contrato de Podcast Index; también se envían `X-Auth-Key` y un `User-Agent` estable. El secreto nunca se persiste en SQLite, logs, errores, fixtures ni URLs.
- Si faltan credenciales, Explorar/Buscar explica cómo configurarlas, mientras RSS, biblioteca local, descargas y reproducción siguen disponibles.
- Reloj inválido, 401/403, 429 y 5xx son errores tipados. Para 429 se respeta `Retry-After` acotado, sin bucle automático desde la UI; no se registran cuerpos potencialmente sensibles.

### Política HTTP(S) común

- Sólo `https` para Podcast Index, imágenes y media remota. Para RSS público se admite `https` y, por compatibilidad con feeds existentes, `http`; la UI marca un feed HTTP como conexión no cifrada antes de confirmar. Una cadena iniciada en HTTPS nunca puede degradar a HTTP. Tras cada redirección se vuelve a validar esquema, puerto, host y destino.
- Se permiten los puertos predeterminados 443/80 o un puerto explícito de la URL RSS; se bloquean URLs con usuario/contraseña, fragmentos, host vacío o literal IP local.
- Para reducir SSRF en RSS suministrado por el usuario se rechazan, antes de conectar y en cada redirección, loopback, link-local, multicast, unspecified, rangos privados/reservados IPv4/IPv6 y nombres que resuelvan a cualquiera de ellos. Se limita a 5 redirecciones y se protege contra DNS rebinding usando las direcciones validadas por el transporte.
- TLS usa el almacén CA incluido/documentado, verifica certificado y hostname y no ofrece modo “inseguro”. TLS o certificado fallido nunca cae a HTTP.
- Timeouts por defecto: conexión 10 s, lectura 20 s y total 60 s para API/RSS/imágenes. Las descargas de audio tienen conexión 10 s, inactividad 30 s y no tienen un total corto, pero son cancelables.
- Máximos: 2 MiB por respuesta JSON de catálogo, 5 MiB por RSS/XML, 4 MiB por imagen y un límite de descarga por episodio configurable (por defecto 2 GiB) más comprobación del espacio libre. Se corta el flujo al superar el límite aunque `Content-Length` falte o mienta.
- Se aceptan respuestas comprimidas sólo con límite sobre bytes descomprimidos. Se restringen métodos a GET y HEAD, se codifican parámetros y no se concatenan consultas manualmente.
- JSON se valida por forma, tipos y número máximo de resultados antes de persistir. XML se procesa con entidades externas, DTD, expansión de entidades y acceso de red deshabilitados; el parser trabaja sobre el cuerpo ya acotado. Feed y enclosure deben superar validación semántica.
- ETag/Last-Modified se usan para solicitudes condicionales. Un 304 conserva la versión previa. Una respuesta nueva sólo sustituye caché y datos dentro de una transacción después de validar por completo.
- La caché tiene TTL explícito por tipo. Una respuesta caducada puede mostrarse como “datos guardados” si la red falla, nunca como actualización reciente.
- Los nombres de archivo se derivan del UUID local, no de título ni URL. Todas las rutas se resuelven y verifican bajo `data/`; no se siguen rutas procedentes del feed.

## 7. Importación RSS y actualización

`docs/RSS-IMPORT.md` documentará un flujo sin teclado: apagar/sacar la SD o acceder a ella por el medio disponible, añadir una URL HTTPS por línea a `data/imports/rss_urls.txt`, insertar la SD e iniciar AnberPod. Se ignoran líneas vacías y comentarios `#`; se limita a 100 líneas y 2048 caracteres por URL. El archivo se lee bajo demanda desde Ajustes > Importar RSS, no silenciosamente al arrancar.

Cada URL se normaliza, pasa la política SSRF, se descarga y parsea, y se muestra una previsualización antes de suscribir. Los resultados por línea (`OK`, `DUPLICADA` o código de error sin credenciales) se escriben atómicamente en `rss_urls.result.txt`; el archivo fuente no se borra. Una URL duplicada abre el podcast existente. Un fallo de una línea no revierte otras importaciones válidas.

“Actualizar” existe en el detalle de un podcast y en Suscripciones para actualizar todas secuencialmente con cancelación. Sólo es bajo demanda. No hay temporizador de actualización, descarga implícita ni trabajo de red al entrar en una pantalla.

## 8. Descargas offline

La acción “Descargar” crea una fila `queued` sólo después de comprobar URL HTTPS, límite configurado y espacio libre (tamaño conocido más un margen; si es desconocido, el máximo configurado). Un único trabajador pasa a `downloading`, escribe `<uuid>.part` en bloques, limita tamaño real y emite progreso a la UI. La v1 usa una descarga simultánea para evitar presión de memoria, almacenamiento y red.

Al terminar, el trabajador sincroniza el archivo, comprueba que haya bytes, valida el contenedor con el `ffmpeg` empaquetado sin decodificarlo completo y renombra atómicamente a `<uuid>.<ext-segura>`; sólo entonces marca `complete`. Error, cancelación, falta de espacio, media no válida o reinicio conservan diagnóstico y nunca crean un falso completo. Cuando servidor y validadores lo permiten, “Reintentar” reanuda con `Range` y exige una respuesta 206/`Content-Range` coherente; en cualquier ambigüedad reinicia el `.part` desde cero.

El borrado requiere confirmación, detiene una reproducción de ese archivo o se rechaza mientras está en uso, elimina sólo archivo/temporal y fila de descarga en una operación tolerante a reinicios, y conserva suscripción, metadatos y progreso. No hay limpieza automática por edad, espacio o actualización.

## 9. Reproducción y guardado de progreso

`PlaybackService` elige archivo completo local antes que media HTTPS. Para remoto, valida de nuevo la URL y lanza el `ffmpeg` empaquetado con lista blanca de protocolos y opciones de reconexión/timeout acotadas; para local, pasa una ruta verificada bajo `data/downloads`. Los argumentos se entregan como lista, nunca mediante shell. `ffmpeg` escribe PCM por stdout y `aplay` recibe ese PCM por stdin; stderr se captura de forma acotada para diagnóstico y ningún proceso hereda secretos.

Una sesión tiene estados `idle`, `buffering`, `playing`, `paused`, `stopped`, `ended` y `error`. A reproduce/pausa; detener es una acción explícita del panel de reproducción; izquierda/derecha saltan −15/+30 segundos, acotados entre cero y duración conocida. Seek reinicia de forma controlada el pipeline con `-ss` en la posición solicitada. Stop, error y salida terminan ambos procesos con plazo, y luego escalamiento controlado, sin huérfanos.

La posición se obtiene del reloj monotónico y de eventos del motor, no de fotogramas SDL. Se persiste en una transacción cada 10 segundos mientras reproduce y también al pausar, hacer seek, detener, recibir MENU, terminar o gestionar una salida normal. Escrituras se agrupan para no castigar la SD. Al finalizar se marca `completed=1`; reabrir un episodio completado empieza en cero sólo tras confirmación. Una posición mayor que duración o media cambiante se acota. Un cierre eléctrico puede perder como máximo el último intervalo de 10 segundos, no corromper el estado confirmado.

Si falla el streaming, la UI conserva la última posición confirmada y ofrece reintentar; no descarga automáticamente. La pausa suspende el flujo de audio de forma verificable sin avanzar el contador. El cambio local/remoto no altera la misma fila de progreso del episodio.

## 10. Entrada, navegación e interfaz 640×480

La UI usa resolución lógica fija 640×480 y escala manteniendo proporción. Todo texto importante se representa con fuentes incluidas y Pillow/PySDL2, alto contraste, foco visible, truncado con elipsis y desplazamiento de listas; ninguna acción exige hover, táctil o teclado.

Mapa global:

| Control | Acción |
|---|---|
| Cruceta arriba/abajo | mover foco o elemento de lista |
| Cruceta izquierda/derecha | cambiar pestaña/valor; en reproductor, −15/+30 s |
| A | aceptar; en episodio/reproductor, reproducir/pausar según contexto |
| B | volver una pantalla; cerrar diálogo sin confirmar |
| MENU | guardar progreso, detener limpiamente procesos y salir desde cualquier pantalla |

Se normaliza key-down y se ignora repetición accidental de A/B/MENU; la cruceta permite repetición con retardo. Los códigos SDL concretos se configuran tras capturarlos en hardware y se mantienen en una tabla aislada, con perfil de teclado sólo para desarrollo.

Ruta de pantallas:

```text
Inicio
├── Explorar -> Categorías -> Resultados -> Podcast -> Episodios -> Reproductor
├── Buscar -> Teclado virtual -> Resultados -> Podcast -> Episodios -> Reproductor
├── Suscripciones -> Podcast -> Episodios -> Reproductor
├── Descargas -> Episodio/Reproductor
└── Ajustes -> Importar RSS / credenciales detectadas / rutas y versión
```

Buscar usa teclado virtual manejable con cruceta, A (insertar) y B (borrar/volver mediante foco explícito), además de historial local opcional no sensible; nunca presupone teclado físico. Importar URL no usa ese teclado: usa el archivo documentado. Cada lista restaura foco y desplazamiento al volver. Acciones destructivas o de baja muestran confirmación. Indicadores distinguen sin ambigüedad suscrito, descargando, descargado, progreso, offline, carga y error. Los errores de red no sustituyen contenido local ni bloquean navegación.

## 11. Lanzador, paquete y despliegue

El artefacto será un tar/zip con esta disposición de destino:

```text
Roms/APPS/
├── AnberPod.sh
└── AnberPod/
    ├── current/                    # aplicación Python, dependencias y assets
    ├── runtime/bin/ffmpeg          # ELF ARM64 estático y ejecutable
    ├── runtime/certs/cacert.pem    # CA versionada si MuOS no ofrece una fiable
    ├── data/                       # se crea sólo si falta; no se empaqueta con contenido
    └── README-INSTALL.txt
```

`AnberPod.sh` será POSIX `sh`, resolverá su propio directorio sin depender del directorio de trabajo, definirá rutas absolutas de Python/app/datos/ffmpeg/CA, configurará SDL para la pantalla y ALSA sólo con valores comprobados en MuOS, creará directorios ausentes con permisos restrictivos, y redirigirá arranque y errores al log rotado. Verifica Python 3.10, ejecutables y escritura en datos; ante fallo escribe un diagnóstico legible y sale distinto de cero.

El bundle incluirá bytecode/fuentes Python y wheels puros o preconstruidos compatibles con ARM64; no hará `pip install` ni acceso de red en la consola. `ffmpeg` debe ser ARM64 estático, con licencia y procedencia documentadas, soporte de TLS/HTTPS y únicamente los protocolos/demuxers/decoders necesarios. `aplay` y el dispositivo ALSA se detectan durante la validación de instalación; si `aplay` no está disponible, la instalación falla con instrucciones, pues no se cambia silenciosamente a un reproductor del firmware.

Una actualización publica primero `releases/<versión>` o `current.new`, valida contenido y cambia el selector de versión de forma atómica cuando sea posible. Nunca incluye `data/db`, `data/downloads`, `data/cache`, `data/imports`, `data/config` ni `data/logs`; tampoco ejecuta `rm` sobre `data`. Antes y después se calcula una huella de esos datos en la prueba de actualización. Debe existir una ruta de rollback del código que reutilice el mismo esquema cuando la migración sea compatible; las migraciones incompatibles requieren copia y procedimiento explícito.

## 12. Fases y puertas de salida

### Fase 0 — esqueleto reproducible y contratos

Definir paquete Python 3.10, dependencias fijadas, modelos, puertos, configuración, logging con redacción, rutas de datos y tests de arquitectura. Crear un lanzador de diagnóstico que abre/cierra SDL y registra el arranque, todavía sin red ni audio.

Puerta: tests unitarios sin red; bundle inspeccionable; arranque desde ruta con espacios; MENU cierra y deja log; escaneo confirma ausencia de secretos y media.

### Fase 1 — persistencia y biblioteca offline

Implementar esquema/migración, repositorios, pantalla Inicio/Suscripciones/Descargas/Ajustes con datos de fixture, navegación física y arranque offline. Añadir importación de archivo sólo hasta validación/previsualización con transporte falso.

Puerta: migraciones idempotentes, recuperación tras caché/`.part` corruptos, navegación completa sin teclado ni red y actualización simulada que conserva byte por byte los datos.

### Fase 2 — RSS directo y suscripciones

Implementar transporte endurecido, parser RSS/Atom, importación real, detalle, episodios, alta/baja y actualización manual condicional. No incorporar todavía Podcast Index.

Puerta: fixtures válidos y hostiles; límites/redirecciones/SSRF/TLS cubiertos; importar, suscribir, actualizar y desuscribir conserva episodios/progreso.

### Fase 3 — Podcast Index y descubrimiento

Implementar proveedor de credenciales, firma, categorías, búsqueda, caché y pantallas Explorar/Buscar con teclado virtual. Abrir resultado y suscribirse por su feed.

Puerta: vectores deterministas de firma; 401/429/5xx/offline; ningún secreto en logs; criterios de categorías, búsqueda, apertura y suscripción en host y dispositivo.

### Fase 4 — reproducción y reanudación

Integrar el binario ffmpeg fijado, `aplay`, motor de procesos, panel de reproducción, streaming HTTPS, seek y persistencia periódica. Probar primero con audio sintético HTTPS de licencia compatible.

Puerta: no se usa reproductor del firmware; play/pause/stop/seek; procesos sin huérfanos; reanudación tras salida y reinicio con pérdida máxima de 10 segundos; error remoto no destruye posición.

### Fase 5 — descargas y reproducción offline

Implementar trabajador único, límites, `.part`, reanudación segura, validación, publicación atómica, prioridad local y borrado manual aislado.

Puerta: corte de red/espacio/reinicio; archivo parcial nunca reproducido; apagar red reproduce el completo; borrar conserva suscripción e historial; no hay automatismos de descarga o borrado.

### Fase 6 — empaquetado y aceptación RG35XX H

Fijar perfil SDL/input/ALSA de MuOS, construir bundle ARM64, documentar instalación/importación/credenciales/licencias, ejecutar actualización y matriz de aceptación en SD real.

Puerta: todas las pruebas de la sección 13, arranque desde `Roms/APPS`, logs útiles, funcionamiento offline y actualización no destructiva en dos ciclos consecutivos.

## 13. Pruebas exactas y comandos de validación

Los nombres siguientes forman el contrato del futuro repositorio. Los comandos se ejecutan desde su raíz con Python 3.10; ninguno de los tests unitarios/integración depende de Internet.

### Automatización de host

```sh
python3.10 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m ruff check src tests
.venv/bin/python -m mypy --strict src/anberpod
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/unit
.venv/bin/python -m pytest -q tests/integration
.venv/bin/python -m pytest -q tests/contract
.venv/bin/python -m pytest -q --cov=anberpod --cov-branch --cov-fail-under=85
```

Casos mínimos obligatorios, con nombres estables:

- `test_schema_migrates_empty_db_and_is_idempotent`
- `test_failed_migration_rolls_back_and_preserves_backup`
- `test_unsubscribe_preserves_episodes_playback_and_downloads`
- `test_delete_download_preserves_subscription_and_playback`
- `test_episode_upsert_uses_guid_url_then_fallback_key`
- `test_startup_offline_renders_valid_local_library`
- `test_corrupt_cache_does_not_replace_last_valid_data`
- `test_atomic_cache_interruption_keeps_previous_file`
- `test_import_file_handles_comments_duplicates_and_per_line_errors`
- `test_import_rejects_url_credentials_and_overlong_url`
- `test_http_feed_warns_and_https_redirect_never_downgrades`
- `test_rss_parser_accepts_rss2_atom_and_common_namespaces`
- `test_rss_parser_rejects_dtd_entities_external_access_and_oversize_body`
- `test_http_rechecks_https_and_public_address_after_every_redirect`
- `test_http_rejects_private_loopback_linklocal_ipv4_and_ipv6`
- `test_http_enforces_timeouts_redirect_limit_and_decompressed_size`
- `test_tls_failure_never_falls_back_to_http`
- `test_conditional_get_304_preserves_cached_feed`
- `test_podcast_index_signature_matches_fixed_vector`
- `test_missing_catalog_credentials_leaves_local_features_available`
- `test_logs_redact_api_secret_authorization_and_query_values`
- `test_rate_limit_is_typed_and_does_not_busy_retry`
- `test_navigation_focus_back_and_menu_work_without_keyboard`
- `test_virtual_keyboard_can_enter_search_using_dpad_a_b`
- `test_menu_persists_progress_and_shuts_down_workers`
- `test_player_prefers_complete_local_file_over_remote_url`
- `test_player_never_selects_part_or_missing_download`
- `test_pause_does_not_advance_position_and_seek_is_bounded`
- `test_progress_saved_every_ten_seconds_pause_seek_stop_and_exit`
- `test_ffmpeg_and_aplay_arguments_are_lists_and_protocols_are_limited`
- `test_player_terminates_both_children_on_error_and_exit`
- `test_download_rejects_insufficient_space_known_and_unknown_length`
- `test_download_enforces_stream_limit_when_content_length_lies`
- `test_download_only_becomes_complete_after_fsync_probe_and_atomic_rename`
- `test_range_resume_requires_coherent_206_otherwise_restarts`
- `test_interrupted_download_is_not_playable_and_can_retry`
- `test_no_use_case_starts_automatic_download_or_deletion`

Los tests HTTP usarán un servidor TLS local con CA de prueba y un resolvedor falso controlable; no se desactivará la verificación TLS. Los tests del adaptador de procesos usarán ejecutables espía, no el audio del host. Fixtures de XML incluyen cuerpos truncados, content types erróneos, redirecciones, compresión bomba acotada y entidades maliciosas.

### Bundle y arquitectura

```sh
./scripts/build_bundle.sh --arch aarch64 --version 0.1.0
./scripts/check_bundle.sh dist/AnberPod-0.1.0-aarch64.tar.gz
tar -tf dist/AnberPod-0.1.0-aarch64.tar.gz
file build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg
readelf -h build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg
build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg -hide_banner -protocols
build/stage/Roms/APPS/AnberPod/runtime/bin/ffmpeg -hide_banner -buildconf
rg -n --hidden -i '(api[_-]?secret|authorization|BEGIN .*PRIVATE KEY|\.mp3|\.m4a|\.ogg)' build/stage dist
```

`check_bundle.sh` debe fallar si el ELF no es AArch64, ffmpeg no es estático conforme al método documentado, faltan HTTPS/TLS o licencia, hay rutas absolutas de build, el paquete contiene secretos/media/DB/datos de usuario, el launcher no es ejecutable, o aparece un reproductor prohibido. El `rg` final permite únicamente nombres de campos vacíos/documentación y manifiestos de prueba expresamente listados; cualquier coincidencia inesperada falla el job.

Prueba exacta de preservación durante actualización sobre un directorio temporal (implementada por `tests/integration/test_upgrade_bundle.py`):

```sh
.venv/bin/python -m pytest -q tests/integration/test_upgrade_bundle.py
```

La prueba instala v0.1.0, crea configuración, DB, caché, progreso, descarga, import y log centinela, calcula SHA-256 y metadatos, instala v0.1.1 y comprueba que ningún archivo de `data/` fue sobrescrito o eliminado y que una migración sólo cambió la DB de la manera esperada.

### Validación en RG35XX H con MuOS

Copiar primero a una SD de prueba y ejecutar desde el menú, no sólo desde SSH. Conservar en `docs/HARDWARE-VALIDATION.md` modelo, versión MuOS, filesystem, hash del bundle y resultado de cada paso.

```sh
cd /mnt/mmc/Roms/APPS/AnberPod
./current/bin/python3 --version
file runtime/bin/ffmpeg
./runtime/bin/ffmpeg -hide_banner -version
command -v aplay
aplay -l
test -w data
tail -n 200 data/logs/anberpod.log
```

La ruta `/mnt/mmc` es un marcador que se sustituirá por la ruta real descubierta en el dispositivo; el lanzador nunca la codifica. Matriz manual obligatoria:

1. Iniciar desde `APPS` sin red; confirmar log de arranque, Inicio y datos locales.
2. Recorrer cada pantalla sólo con cruceta/A/B; MENU desde cada pantalla guarda y sale sin proceso `ffmpeg`, `aplay` o Python huérfano.
3. Con credenciales locales, abrir categorías, buscar con teclado virtual, abrir podcast, suscribirse y desuscribirse sin perder historial.
4. Importar desde `rss_urls.txt`, revisar resultado, suscribirse y actualizar episodios bajo demanda.
5. Reproducir HTTPS, pausar, saltar −15/+30, detener, reiniciar app y verificar reanudación dentro de ±10 s.
6. Descargar manualmente, observar tamaño/estado, apagar red y reproducir el archivo local completo.
7. Cortar red y energía durante otra descarga; reiniciar y confirmar que `.part` no se reproduce y que Reintentar funciona sin falso completo.
8. Borrar una descarga y confirmar que suscripción, episodio y posición siguen presentes.
9. Llenar la SD hasta el margen de seguridad y confirmar rechazo limpio, sin corrupción ni eliminación automática.
10. Instalar la siguiente versión sobre una biblioteca poblada; comparar manifiesto/hash de datos y repetir arranque offline.
11. Dejar reproducir y navegar durante 60 minutos; confirmar UI responsiva, audio sin degradación sostenida, memoria acotada, temperatura razonable y logs rotados.

## 14. Riesgos que deben resolverse antes de congelar el paquete

- Capturar en el hardware real los códigos SDL de cruceta/A/B/MENU, el driver de vídeo, el dispositivo ALSA y la presencia/comportamiento de `aplay`; son datos de plataforma, no deben adivinarse en código.
- Verificar que el build estático elegido de ffmpeg para AArch64 tiene HTTPS, codecs habituales de podcasts (MP3, AAC/M4A, Opus/Vorbis) y licencia redistribuible compatible, manteniendo tamaño razonable.
- Medir rendimiento de decodificación, presión de escritura y duración de batería con streaming y archivo local. Si 48 kHz estéreo no es estable, cambiar el formato PCM una sola vez tras medición y actualizar contrato/tests.
- Confirmar semántica de actualización y rutas de SD de la versión MuOS objetivo. El invariante no negociable es que `data/` quede fuera del contenido reemplazable.
- Probar certificados y reloj del dispositivo: un reloj incorrecto debe producir diagnóstico accionable, nunca desactivar TLS ni falsificar autenticación.

## 15. Trazabilidad de aceptación

| Criterio | Fase | Evidencia principal |
|---|---:|---|
| Categorías, búsqueda, abrir y suscribir | 3 | tests catálogo/navegación + matriz 3 |
| Importar RSS, suscribir y ver episodios | 2 | tests import/parser/repositorio + matriz 4 |
| Streaming, detener y reanudar | 4 | tests motor/progreso + matriz 5 |
| Descargar, apagar red y reproducir SD | 5 | tests prioridad/atomicidad + matriz 6–7 |
| Borrar descarga sin perder biblioteca/historial | 5 | tests de aislamiento + matriz 8 |
| Arrancar desde `Roms/APPS`, registrar y preservar datos al actualizar | 0, 6 | `check_bundle`, test upgrade + matrices 1, 10 |

No se añaden prestaciones fuera de alcance para “completar” una fase. Cualquier cambio de esquema, red, reproducción o despliegue exige primero actualizar sus contratos, prueba de fallo y evidencia en hardware correspondiente.
