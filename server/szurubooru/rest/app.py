import urllib.parse
import cgi
import json
import re
from datetime import datetime
from szurubooru.func import util
from szurubooru.rest import errors, middleware, routes, context


def _json_serializer(obj):
    ''' JSON serializer for objects not serializable by default JSON code '''
    if isinstance(obj, datetime):
        serial = obj.isoformat('T') + 'Z'
        return serial
    raise TypeError('Type not serializable')


def _dump_json(obj):
    return json.dumps(obj, default=_json_serializer, indent=2)


def _get_headers(env):
    headers = {}
    for key, value in env.items():
        if key.startswith('HTTP_'):
            key = util.snake_case_to_upper_train_case(key[5:])
            headers[key] = value
    return headers


def _create_context(env):
    method = env['REQUEST_METHOD']
    path = urllib.parse.unquote('/' + env['PATH_INFO'].lstrip('/'))
    headers = _get_headers(env)

    files = {}
    params = dict(urllib.parse.parse_qsl(env.get('QUERY_STRING', '')))

    if 'multipart' in env.get('CONTENT_TYPE', ''):
        form = cgi.FieldStorage(fp=env['wsgi.input'], environ=env)
        if not form.list:
            raise errors.HttpBadRequest('No files attached.')
        body = form.getvalue('metadata')
        for key in form:
            files[key] = form.getvalue(key)
    else:
        body = env['wsgi.input'].read()

    if body:
        try:
            if isinstance(body, bytes):
                body = body.decode('utf-8')

            for key, value in json.loads(body).items():
                params[key] = value
        except (ValueError, UnicodeDecodeError):
            raise errors.HttpBadRequest(
                'Could not decode the request body. The JSON '
                'was incorrect or was not encoded as UTF-8.')

    return context.Context(method, path, headers, params, files)


def application(env, start_response):
    try:
        try:
            ctx = _create_context(env)
            if 'application/json' not in ctx.get_header('Accept'):
                raise errors.HttpNotAcceptable(
                    'This API only supports JSON responses.')

            for url, allowed_methods in routes.routes.items():
                match = re.fullmatch(url, ctx.url)
                if not match:
                    continue
                if ctx.method not in allowed_methods:
                    raise errors.HttpMethodNotAllowed(
                        'Allowed methods: %r' % allowed_methods)

                for hook in middleware.pre_hooks:
                    hook(ctx)
                handler = allowed_methods[ctx.method]
                try:
                    response = handler(ctx, match.groupdict())
                finally:
                    for hook in middleware.post_hooks:
                        hook(ctx)

                start_response('200', [('content-type', 'application/json')])
                return (_dump_json(response).encode('utf-8'),)

            raise errors.HttpNotFound(
                'Requested path ' + ctx.url + ' was not found.')

        except Exception as ex:
            for exception_type, handler in errors.error_handlers.items():
                if isinstance(ex, exception_type):
                    handler(ex)
            raise

    except errors.BaseHttpError as ex:
        start_response(
            '%d %s' % (ex.code, ex.reason),
            [('content-type', 'application/json')])
        blob = {
            'name': ex.name,
            'title': ex.title,
            'description': ex.description,
        }
        if ex.extra_fields is not None:
            for key, value in ex.extra_fields.items():
                blob[key] = value
        return (_dump_json(blob).encode('utf-8'),)
