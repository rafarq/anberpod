# AnberPod — requisitos confirmados

## Producto
Reproductor nativo de podcasts para Anbernic RG35XX H con MuOS, instalado desde el menú `APPS`. Debe poder descubrir podcasts, suscribirse a ellos, escuchar episodios y mantener el progreso de cada episodio únicamente en el dispositivo.

## Alcance del MVP

1. **Inicio**: acceso a Explorar, Buscar, Suscripciones, Descargas y ajustes.
2. **Descubrir**: categorías, búsqueda por texto y resultados provenientes de Podcast Index.
3. **RSS directo**: añadir una URL de feed RSS como fuente adicional, validarla y obtener su metadato y episodios.
4. **Suscripciones**: alta y baja local; episodios por podcast y actualización bajo demanda.
5. **Reproducción**: streaming HTTPS; reproducir/pausar/detener; avance/retroceso; guardar posición con reanudación.
6. **Descargas offline**: descarga manual de cada episodio, estado y tamaño; reproducir archivo local preferentemente; borrado manual. No habrá eliminación automática ni descargas automáticas.
7. **Persistencia local**: suscripciones, posiciones de reproducción, descargas y cachés en la tarjeta SD. Las actualizaciones del programa nunca deben sobrescribir estos datos.

## Interacción y equipo

- Hardware: Anbernic RG35XX H.
- Firmware: MuOS.
- Pantalla lógica: 640×480.
- Navegación con botones físicos: cruceta, A para aceptar/reproducir, B para volver, MENU para salir.
- Debe funcionar sin teclado en la consola. La entrada de una URL RSS puede hacerse mediante un archivo de importación documentado en la tarjeta SD.

## Fuentes externas

- Catálogo y categorías: Podcast Index, utilizando credenciales desde un fichero local fuera del repositorio o configuración del usuario.
- RSS directos: feeds públicos suministrados por el usuario.
- HTTP(S) con verificación TLS, límites de tiempo y tamaño, validación de XML y caché atómica.

## Restricciones técnicas

- Python 3.10, PySDL2 y Pillow compatibles con MuOS.
- No depender de reproductores multimedia que incluya el firmware.
- Incluir un `ffmpeg` estático ARM64 configurable o documentar el destino del binario; decodificar a PCM reproducido por ALSA/aplay.
- La aplicación ha de poder iniciar sin conexión mostrando datos locales válidos.
- El repositorio no contiene credenciales, podcasts privados ni descargas.

## Fuera de alcance inicial

- Cuentas de usuario, sincronización entre dispositivos y analítica.
- Recomendaciones personalizadas.
- Descargas automáticas, colas y reproducción a velocidad variable.

## Criterios de aceptación

- Un usuario puede navegar categorías, buscar, abrir un podcast y suscribirse.
- Puede importar un RSS, suscribirse y ver sus episodios.
- Puede reproducir un episodio remoto, parar y reanudar desde la posición guardada.
- Puede descargar manualmente un episodio, apagar la red y reproducirlo desde la SD.
- Puede borrar una descarga sin perder la suscripción ni el historial.
- La app funciona desde `Roms/APPS`, registra arranque y errores, y no sobrescribe estado local al actualizar.
