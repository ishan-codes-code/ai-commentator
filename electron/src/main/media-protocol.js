export function registerProjectMediaProtocol({ protocol, net, pathToFileURL, app, resolveProjectMedia }) {
  protocol.handle('project', (request) => {
    if (request.method !== 'GET') {
      return new Response('Not found', { status: 404 });
    }
    try {
      const url = new URL(request.url);
      const segments = url.pathname.split('/').filter(Boolean);
      // project://<id>/frames/<name> or project://<id>/audio/<scope>/<name>
      const [kind, ...rest] = segments;
      let scope = null;
      let name = null;
      if (kind === 'frames' && rest.length === 1) {
        scope = 'frames';
        [name] = rest;
      } else if (kind === 'audio' && rest.length === 2) {
        const [audioScope, audioName] = rest;
        if (audioScope !== 'audio' && audioScope !== 'commentary') {
          return new Response('Not found', { status: 404 });
        }
        scope = audioScope === 'audio' ? 'audio' : 'audio/commentary';
        name = audioName;
      } else {
        return new Response('Not found', { status: 404 });
      }
      const filePath = resolveProjectMedia(
        app.getPath('userData'),
        url.hostname,
        scope,
        name,
      );
      return net.fetch(pathToFileURL(filePath).toString());
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

