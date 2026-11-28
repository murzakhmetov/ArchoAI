import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:archoai_app/services/tripo_service.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _aiBase = 'https://openapi.tripo3d.ai/v3';
const _comBase = 'https://openapi.tripo3d.com/v3';

/// Minimal valid GLB: header ("glTF", version 2, total length 24) followed by
/// one JSON chunk ("{}  ").
final Uint8List _glb = Uint8List.fromList([
  0x67, 0x6C, 0x54, 0x46, 2, 0, 0, 0, 24, 0, 0, 0, // header
  4, 0, 0, 0, 0x4A, 0x53, 0x4F, 0x4E, // chunk length 4, type "JSON"
  0x7B, 0x7D, 0x20, 0x20, // "{}  "
]);

/// Same file but the header declares 1000 bytes: a truncated download.
final Uint8List _truncatedGlb = Uint8List.fromList([
  0x67, 0x6C, 0x54, 0x46, 2, 0, 0, 0, 0xE8, 0x03, 0, 0,
  4, 0, 0, 0, 0x4A, 0x53, 0x4F, 0x4E,
  0x7B, 0x7D, 0x20, 0x20,
]);

/// Only the 12-byte header: no chunk, so not a usable model.
final Uint8List _headerOnlyGlb = Uint8List.fromList(
  [0x67, 0x6C, 0x54, 0x46, 2, 0, 0, 0, 12, 0, 0, 0],
);

http.Response _json(
  Object body, {
  int status = 200,
  Map<String, String> headers = const {},
}) =>
    http.Response(
      jsonEncode(body),
      status,
      headers: {'content-type': 'application/json', ...headers},
    );

Map<String, dynamic> _ok(Map<String, dynamic> data) =>
    {'code': 0, 'data': data};

Map<String, dynamic> _task(
  String status, {
  int progress = 0,
  Map<String, dynamic>? output,
  int? errorCode,
  String? errorMessage,
}) =>
    _ok({
      'task_id': 't1',
      'type': 'image_to_model',
      'status': status,
      'progress': progress,
      'output': ?output,
      'error_code': ?errorCode,
      'error_message': ?errorMessage,
    });

final Map<String, dynamic> _success = _task('success', progress: 100, output: {
  'model_url': 'https://cdn.example/m.glb',
});

/// Fake clock: sleeping advances time instantly and records the delay.
class _Clock {
  final DateTime start = DateTime(2026, 11, 13);
  late DateTime now = start;
  final List<Duration> delays = [];

  Future<void> delay(Duration d) async {
    delays.add(d);
    now = now.add(d);
  }

  Duration get elapsed => now.difference(start);
}

TripoService _service(
  MockClient client,
  _Clock clock, {
  String baseUrl = TripoService.globalBaseUrl,
  int? faceLimit,
  String apiKey = 'test-key',
  Duration requestTimeout = const Duration(seconds: 30),
  Duration downloadTimeout = const Duration(seconds: 60),
}) =>
    TripoService(
      apiKey: apiKey,
      baseUrl: baseUrl,
      client: client,
      faceLimit: faceLimit,
      requestTimeout: requestTimeout,
      downloadTimeout: downloadTimeout,
      delay: clock.delay,
      now: () => clock.now,
    );

void main() {
  late Directory tmp;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('tripo_test_');
  });

  tearDown(() {
    if (tmp.existsSync()) tmp.deleteSync(recursive: true);
  });

  group('uploadImage', () {
    test('posts multipart to /files with bearer key and image content type',
        () async {
      final image = File('${tmp.path}/photo.jpg')
        ..writeAsBytesSync(List.filled(32, 7));
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_ok({'file_token': 'file_abc'}));
      });

      final token = await _service(client, _Clock()).uploadImage(image);

      expect(token, 'file_abc');
      expect(seen!.method, 'POST');
      expect(seen!.url.toString(), '$_aiBase/files');
      expect(seen!.headers['Authorization'], 'Bearer test-key');
      expect(seen!.headers['content-type'], startsWith('multipart/form-data'));
      final body = latin1.decode(seen!.bodyBytes).toLowerCase();
      expect(body, contains('name="file"'));
      expect(body, contains('content-type: image/jpeg'));
    });

    test('rejects unsupported formats and empty files without a request',
        () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(_ok({'file_token': 'x'}));
      });
      final service = _service(client, _Clock());

      final heic = File('${tmp.path}/photo.heic')..writeAsBytesSync([1, 2, 3]);
      final empty = File('${tmp.path}/empty.png')..writeAsBytesSync([]);

      await expectLater(service.uploadImage(heic), throwsA(isA<TripoException>()));
      await expectLater(service.uploadImage(empty), throwsA(isA<TripoException>()));
      expect(calls, 0);
    });

    test('retries a dropped connection because uploads are safe to repeat',
        () async {
      final image = File('${tmp.path}/p.png')..writeAsBytesSync([1, 2, 3]);
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) throw const SocketException('Connection reset by peer');
        return _json(_ok({'file_token': 'file_2'}));
      });
      final clock = _Clock();

      expect(await _service(client, clock).uploadImage(image), 'file_2');
      expect(calls, 2);
      expect(clock.delays, [const Duration(seconds: 1)]);
    });
  });

  group('quality options', () {
    const best = {
      'TRIPO_GEOMETRY_QUALITY': 'detailed',
      'TRIPO_TEXTURE_QUALITY': 'detailed',
      'TRIPO_PBR': 'true',
      'TRIPO_TEXTURE_ALIGNMENT': 'original_image',
      'TRIPO_COMPRESS': 'geometry',
    };

    test('best-quality settings from .env are sent with the task', () async {
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_ok({'task_id': 'task-q'}));
      });
      final service = TripoService(
        apiKey: 'test-key',
        client: client,
        quality: TripoService.qualityFromEnv((name) => best[name]),
      );

      await service.createImageToModelTaskFromToken('file_abc');

      expect(jsonDecode(seen!.body), {
        'input': 'file_abc',
        'model': 'v3.1-20260211',
        'geometry_quality': 'detailed',
        'texture_quality': 'detailed',
        'pbr': true,
        'texture_alignment': 'original_image',
        'compress': 'geometry',
      });
    });

    test('unset and unsupported values are left out', () {
      const env = {'TRIPO_GEOMETRY_QUALITY': 'ultra', 'TRIPO_PBR': 'no', 'TRIPO_TEXTURE_QUALITY': 'EXTREME'};
      expect(TripoService.qualityFromEnv((name) => env[name]), {'pbr': false, 'texture_quality': 'extreme'});
      expect(TripoService.qualityFromEnv((_) => null), isEmpty);
    });
  });

  group('createImageToModelTaskFromToken', () {
    test('uses the v3 endpoint with input + required model', () async {
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_ok({'task_id': 'task-1'}));
      });

      final id = await _service(client, _Clock())
          .createImageToModelTaskFromToken('file_abc');

      expect(id, 'task-1');
      expect(seen!.url.toString(), '$_aiBase/generation/image-to-model');
      expect(seen!.headers['Authorization'], 'Bearer test-key');
      expect(jsonDecode(seen!.body), {
        'input': 'file_abc',
        'model': 'v3.1-20260211',
      });
    });

    test('adds face_limit only when configured', () async {
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_ok({'task_id': 'task-1'}));
      });

      await _service(client, _Clock(), faceLimit: 50000)
          .createImageToModelTaskFromToken('file_abc');

      expect(jsonDecode(seen!.body)['face_limit'], 50000);
    });

    test('fails fast without an API key and sends nothing', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(_ok({'task_id': 'x'}));
      });

      await expectLater(
        _service(client, _Clock(), apiKey: '  ')
            .createImageToModelTaskFromToken('file_abc'),
        throwsA(isA<TripoException>().having((e) => e.isAuthError, 'auth', true)),
      );
      expect(calls, 0);
    });

    test('maps the v3 error envelope (2010 insufficient credits)', () async {
      final client = MockClient((request) async => _json(
            {
              'code': 2010,
              'status': 'error',
              'message': "You don't have enough credit to create this task",
              'suggestion': 'Please purchase more credit',
              'request_id': 'req_abc123',
            },
            status: 403,
          ));

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(
          isA<TripoException>()
              .having((e) => e.code, 'code', 2010)
              .having((e) => e.isInsufficientCredits, 'credits', true)
              .having((e) => e.requestId, 'request id', 'req_abc123')
              .having((e) => e.suggestion, 'suggestion', isNotNull)
              .having((e) => e.message, 'message', contains('Not enough Tripo credits')),
        ),
      );
    });

    test('treats HTTP 200 with a non-zero code as an error', () async {
      final client = MockClient(
        (request) async => _json({'code': 1004, 'message': 'bad param'}),
      );

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>().having((e) => e.code, 'code', 1004)),
      );
    });

    test('non-JSON error pages do not leak HTML into the message', () async {
      final client = MockClient(
        (request) async => http.Response('<html>bad gateway</html>', 502),
      );

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(
          isA<TripoException>()
              .having((e) => e.httpStatus, 'status', 502)
              .having((e) => e.message, 'message', isNot(contains('<html>'))),
        ),
      );
    });

    test('retries on 429 and honours Retry-After', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) {
          return _json(
            {'code': 2000, 'status': 'error', 'message': 'concurrency'},
            status: 429,
            headers: {'retry-after': '3'},
          );
        }
        return _json(_ok({'task_id': 'task-2'}));
      });
      final clock = _Clock();

      final id = await _service(client, clock).createImageToModelTaskFromToken('f');

      expect(id, 'task-2');
      expect(calls, 2);
      expect(clock.delays, [const Duration(seconds: 3)]);
    });

    test('gives up on 429 when Retry-After exceeds the wait budget', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(
          {'code': 2000, 'status': 'error', 'message': 'concurrency'},
          status: 429,
          headers: {'retry-after': '120'},
        );
      });
      final clock = _Clock();

      await expectLater(
        _service(client, clock).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>()
            .having((e) => e.code, 'code', 2000)
            .having((e) => e.isTransient, 'transient', true)),
      );
      expect(calls, 1);
      expect(clock.delays, isEmpty);
    });

    test('stops after three attempts even when Retry-After is 0', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(
          {'code': 1007, 'status': 'error', 'message': 'rate limit'},
          status: 429,
          headers: {'retry-after': '0'},
        );
      });
      final clock = _Clock();

      await expectLater(
        _service(client, clock).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>().having((e) => e.code, 'code', 1007)),
      );
      expect(calls, 3);
      // Retry-After: 0 must not mean "retry instantly": the backoff step wins.
      expect(clock.delays, [const Duration(seconds: 1), const Duration(seconds: 2)]);
    });

    test('never re-sends the paid create call after a 500', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json({'code': 1000, 'message': 'boom'}, status: 500);
      });

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>()
            .having((e) => e.httpStatus, 'status', 500)
            .having((e) => e.message, 'message',
                contains('may still have been created'))),
      );
      expect(calls, 1);
    });

    test('a malformed answer to the create call may hide a created task',
        () async {
      for (final response in [
        http.Response('ok', 200), // not JSON at all
        _json(_ok({})), // success without a task id
      ]) {
        final client = MockClient((request) async => response);
        await expectLater(
          _service(client, _Clock()).createImageToModelTaskFromToken('f'),
          throwsA(isA<TripoException>().having((e) => e.message, 'message',
              contains('may still have been created'))),
        );
      }
    });

    test('an explicit rejection of the create call says nothing was created',
        () async {
      for (final response in [
        _json({'code': 2010, 'message': 'no credit'}, status: 403),
        _json({'code': 1004, 'message': 'bad param'}), // 200 + error code
        _json({'code': 1007, 'message': 'rate'}, status: 429),
      ]) {
        final client = MockClient((request) async => response);
        await expectLater(
          _service(client, _Clock()).createImageToModelTaskFromToken('f'),
          throwsA(isA<TripoException>().having((e) => e.message, 'message',
              isNot(contains('may still have been created')))),
        );
      }
    });

    test('never re-sends the paid create call after a dropped connection',
        () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        throw const SocketException('Connection reset by peer');
      });

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>().having(
          (e) => e.message,
          'message',
          contains('may still have been created'),
        )),
      );
      expect(calls, 1);
    });

    test('never re-sends the paid create call after a timeout', () async {
      var calls = 0;
      final client = MockClient((request) {
        calls++;
        return Completer<http.Response>().future; // never completes
      });

      await expectLater(
        _service(
          client,
          _Clock(),
          requestTimeout: const Duration(milliseconds: 30),
        ).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>()),
      );
      expect(calls, 1);
    });

    test('a failed TLS handshake sends nothing and says so plainly', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        throw const HandshakeException('bad certificate');
      });

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('Could not reach Tripo'))),
      );
      expect(calls, 1);
    });

    test('a TLS error after connecting may have lost the paid request',
        () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        throw const TlsException('connection error mid-stream');
      });

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>().having(
          (e) => e.message,
          'message',
          contains('may still have been created'),
        )),
      );
      expect(calls, 1);
    });

    test('a DNS failure does not switch to another host', () async {
      final hosts = <String>[];
      final client = MockClient((request) async {
        hosts.add(request.url.host);
        throw const SocketException("Failed host lookup: 'openapi.tripo3d.ai'");
      });

      await expectLater(
        _service(client, _Clock()).createImageToModelTaskFromToken('f'),
        throwsA(isA<TripoException>().having(
          (e) => e.message,
          'message',
          // Nothing was sent, so the "task may have been created" warning
          // would be wrong here.
          contains('Could not reach Tripo'),
        )),
      );
      expect(hosts, ['openapi.tripo3d.ai']);
    });
  });

  group('region and configuration', () {
    test('defaults to the global host and keeps a custom base URL tidy', () {
      final client = MockClient((request) async => _json(_ok({})));

      expect(_service(client, _Clock()).baseUrl, _aiBase);
      expect(
        _service(client, _Clock(), baseUrl: ' https://example.test/v3// ').baseUrl,
        'https://example.test/v3',
      );
      expect(_service(client, _Clock(), baseUrl: '  ').baseUrl, _aiBase);
    });

    test('fromEnv reads key, model, face limit, region and base URL', () async {
      dotenv.testLoad(fileInput: '''
TRIPO_API_KEY=env-key
TRIPO_MODEL=v3.0-20250812
TRIPO_FACE_LIMIT=5000
TRIPO_REGION=china
''');
      addTearDown(dotenv.clean);

      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_ok({'task_id': 'task-4'}));
      });

      await TripoService.fromEnv(client: client)
          .createImageToModelTaskFromToken('f');

      expect(seen!.url.toString(), '$_comBase/generation/image-to-model');
      expect(seen!.headers['Authorization'], 'Bearer env-key');
      expect(jsonDecode(seen!.body), {
        'input': 'f',
        'model': 'v3.0-20250812',
        'face_limit': 5000,
      });
    });

    test('TRIPO_BASE_URL wins over TRIPO_REGION; unknown region means global',
        () {
      final client = MockClient((request) async => _json(_ok({})));

      dotenv.testLoad(fileInput: '''
TRIPO_API_KEY=k
TRIPO_REGION=china
TRIPO_BASE_URL=https://example.test/v3/
''');
      addTearDown(dotenv.clean);
      expect(
        TripoService.fromEnv(client: client).baseUrl,
        'https://example.test/v3',
      );

      dotenv.testLoad(fileInput: 'TRIPO_API_KEY=k\nTRIPO_REGION=mars\n');
      expect(TripoService.fromEnv(client: client).baseUrl, _aiBase);

      dotenv.testLoad(
        fileInput: 'TRIPO_API_KEY=k\nTRIPO_FACE_LIMIT=-3\nTRIPO_MODEL=\n',
      );
      final defaults = TripoService.fromEnv(client: client);
      expect(defaults.faceLimit, isNull);
      expect(defaults.model, TripoService.defaultModel);
    });
  });

  group('getTaskStatus / TripoTaskResult', () {
    test('parses a running task', () async {
      final client = MockClient(
        (request) async => _json(_task('RUNNING', progress: 42)),
      );

      final result = await _service(client, _Clock()).getTaskStatus('t1');

      expect(result.status, 'running');
      expect(result.progress, 42);
      expect(result.modelUrl, isNull);
      expect(result.isFailed, isFalse);
      expect(result.isComplete, isFalse);
    });

    test('reads model_url and rendered_image_url from output', () async {
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_task('success', progress: 100, output: {
          'model_url': 'https://cdn.tripo3d.ai/output/model_pbr.glb',
          'rendered_image_url': 'https://cdn.tripo3d.ai/output/preview.png',
        }));
      });

      final result = await _service(client, _Clock()).getTaskStatus('t1');

      expect(seen!.url.toString(), '$_aiBase/tasks/t1');
      expect(result.isComplete, isTrue);
      expect(result.modelUrl, 'https://cdn.tripo3d.ai/output/model_pbr.glb');
      expect(result.renderedImageUrl, 'https://cdn.tripo3d.ai/output/preview.png');
    });

    test('a status that is not a string becomes unknown instead of crashing',
        () async {
      final client = MockClient((request) async => _json(_ok({
            'status': 42,
            'progress': 'half',
          })));

      final result = await _service(client, _Clock()).getTaskStatus('t1');

      expect(result.status, 'unknown');
      expect(result.progress, 0);
      expect(result.isFailed, isFalse);
      expect(result.isSuccess, isFalse);
    });

    test('extractModelUrl prefers model_url and ignores non-URLs', () {
      expect(
        TripoTaskResult.extractModelUrl({
          'model': 'v3.1-20260211',
          'model_url': 'https://cdn.example/m.glb',
        }),
        'https://cdn.example/m.glb',
      );
      // A version string under `model` must never be returned as a URL.
      expect(
        TripoTaskResult.extractModelUrl({'model': 'v3.1-20260211'}),
        isNull,
      );
      // Legacy v2 keys still work when they hold real URLs.
      expect(
        TripoTaskResult.extractModelUrl({'pbr_model': 'https://cdn.example/p.glb'}),
        'https://cdn.example/p.glb',
      );
      expect(TripoTaskResult.extractModelUrl(null), isNull);
      expect(TripoTaskResult.extractModelUrl('https://cdn.example/m.glb'), isNull);
      expect(
        TripoTaskResult.extractModelUrl({'model_url': 'ftp://cdn.example/m.glb'}),
        isNull,
      );
    });

    test('does not search outside data.output', () {
      final result = TripoTaskResult.fromJson('t1', {
        'status': 'success',
        'progress': 100,
        'model_url': 'https://wrong.example/top-level.glb',
        'input': {'model_url': 'https://wrong.example/input.glb'},
      });

      expect(result.modelUrl, isNull);
    });

    test('retries a GET after a 500 (idempotent)', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) return _json({'code': 1000, 'message': 'x'}, status: 500);
        return _json(_task('queued'));
      });
      final clock = _Clock();

      final result = await _service(client, clock).getTaskStatus('t1');

      expect(result.status, 'queued');
      expect(calls, 2);
      expect(clock.delays, [const Duration(seconds: 1)]);
    });

    test('does not sleep for hours because of a huge Retry-After on 5xx',
        () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(
          {'code': 1000, 'message': 'maintenance'},
          status: 503,
          headers: {'retry-after': '86400'},
        );
      });
      final clock = _Clock();

      await expectLater(
        _service(client, clock).getTaskStatus('t1'),
        throwsA(isA<TripoException>()
            .having((e) => e.httpStatus, 'status', 503)
            .having((e) => e.isTransient, 'transient', true)),
      );
      expect(calls, 1);
      expect(clock.delays, isEmpty);
    });

    test('keeps the same host and retries 3 times on DNS failure', () async {
      final urls = <String>[];
      final client = MockClient((request) async {
        urls.add(request.url.toString());
        throw const SocketException("Failed host lookup: 'openapi.tripo3d.ai'");
      });
      final clock = _Clock();

      await expectLater(
        _service(client, clock).getTaskStatus('t1'),
        throwsA(isA<TripoException>().having((e) => e.isTransient, 'transient', true)),
      );
      expect(urls, List.filled(3, '$_aiBase/tasks/t1'));
      // Two sleeps between three attempts, none after the last one.
      expect(clock.delays, [const Duration(seconds: 1), const Duration(seconds: 2)]);
    });

    test('retries a TLS handshake failure on a read call', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) throw const HandshakeException('captive portal');
        return _json(_task('queued'));
      });

      final result = await _service(client, _Clock()).getTaskStatus('t1');

      expect(result.status, 'queued');
      expect(calls, 2);
    });

    test('url-encodes the task id', () async {
      http.Request? seen;
      final client = MockClient((request) async {
        seen = request;
        return _json(_task('queued'));
      });

      await _service(client, _Clock()).getTaskStatus('a/b c');

      expect(seen!.url.path, '/v3/tasks/a%2Fb%20c');
    });
  });

  group('waitForTask', () {
    test('polls until success and reports progress', () async {
      final responses = [
        _task('queued'),
        _task('running', progress: 40),
        _task('success', progress: 100, output: {
          'model_url': 'https://cdn.example/m.glb',
        }),
      ];
      var i = 0;
      final client = MockClient((request) async => _json(responses[i++]));
      final clock = _Clock();
      final progress = <int>[];

      final result = await _service(client, clock)
          .waitForTask('t1', onProgress: progress.add);

      expect(result.modelUrl, 'https://cdn.example/m.glb');
      expect(progress, [0, 40]);
      expect(clock.delays, [
        const Duration(seconds: 2),
        const Duration(seconds: 2),
      ]);
    });

    test('a failed task reports its error_code separately from request codes',
        () async {
      final client = MockClient((request) async => _json(
            _task('failed', errorCode: 2010, errorMessage: 'content policy'),
          ));

      await expectLater(
        _service(client, _Clock()).waitForTask('t1'),
        throwsA(isA<TripoException>()
            .having((e) => e.taskErrorCode, 'taskErrorCode', 2010)
            .having((e) => e.taskFailed, 'taskFailed', true)
            .having((e) => e.code, 'request code', isNull)
            .having((e) => e.isInsufficientCredits, 'credits', false)
            .having((e) => e.message, 'message', contains('content policy'))),
      );
    });

    test('treats cancelled, banned and expired as terminal failures', () async {
      for (final status in ['cancelled', 'banned', 'expired']) {
        final client = MockClient((request) async => _json(_task(status)));
        await expectLater(
          _service(client, _Clock()).waitForTask('t1'),
          throwsA(isA<TripoException>()),
          reason: status,
        );
      }
    });

    test('keeps polling an unknown status until the timeout', () async {
      var polls = 0;
      final client = MockClient((request) async {
        polls++;
        return _json(_task('unknown'));
      });

      await expectLater(
        _service(client, _Clock())
            .waitForTask('t1', timeout: const Duration(seconds: 10)),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('too long'))
            // A timeout says nothing about the task: it may still finish.
            .having((e) => e.taskFailed, 'taskFailed', false)),
      );
      expect(polls, 5);
    });

    test('gives up when success never carries a model URL', () async {
      var polls = 0;
      final client = MockClient((request) async {
        polls++;
        return _json(_task('success', progress: 100, output: {}));
      });

      await expectLater(
        _service(client, _Clock()).waitForTask('t1'),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('no model'))),
      );
      expect(polls, 4);
    });

    test('stops without a request when cancelled', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(_task('running'));
      });

      await expectLater(
        _service(client, _Clock()).waitForTask('t1', isCancelled: () => true),
        throwsA(isA<TripoCancelledException>()),
      );
      expect(calls, 0);
    });

    test('survives a network outage of a minute while polling', () async {
      final clock = _Clock();
      final outageEnds = clock.start.add(const Duration(seconds: 60));
      final client = MockClient((request) async {
        if (clock.now.isBefore(outageEnds)) {
          throw const SocketException('Connection reset by peer');
        }
        return _json(_success);
      });

      final result = await _service(client, clock).waitForTask('t1');

      expect(result.isComplete, isTrue);
      expect(clock.elapsed, greaterThanOrEqualTo(const Duration(seconds: 60)));
    });

    test('gives up once nothing has answered for longer than maxOutage',
        () async {
      final clock = _Clock();
      final client = MockClient((request) async {
        throw const SocketException('Connection reset by peer');
      });

      await expectLater(
        _service(client, clock).waitForTask('t1'),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('Could not reach Tripo'))),
      );
      expect(clock.elapsed, greaterThan(const Duration(minutes: 3)));
      expect(clock.elapsed, lessThan(const Duration(minutes: 4)));
    });

    test('a successful poll resets the outage clock', () async {
      final clock = _Clock();
      var calls = 0;
      // 100 healthy polls take 200 s, longer than maxOutage. An outage that
      // starts after them must still be tolerated.
      final outageEnds = clock.start.add(const Duration(seconds: 260));
      final client = MockClient((request) async {
        calls++;
        if (calls <= 100) return _json(_task('running', progress: 10));
        if (clock.now.isBefore(outageEnds)) {
          throw const SocketException('Connection reset by peer');
        }
        return _json(_success);
      });

      final result = await _service(client, clock).waitForTask('t1');

      expect(result.isComplete, isTrue);
      expect(calls, greaterThan(100));
    });

    test('a 200 page without the API envelope does not abort the wait',
        () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) return http.Response('<html>maintenance</html>', 200);
        return _json(_success);
      });

      final result = await _service(client, _Clock()).waitForTask('t1');

      expect(result.isComplete, isTrue);
      expect(calls, 2);
    });

    test('does not retry non-transient poll errors (bad API key)', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return _json(
          {'code': 2, 'status': 'error', 'message': 'Authentication required'},
          status: 401,
        );
      });

      await expectLater(
        _service(client, _Clock()).waitForTask('t1'),
        throwsA(isA<TripoException>().having((e) => e.isAuthError, 'auth', true)),
      );
      expect(calls, 1);
    });
  });

  group('downloadTaskModel', () {
    const oldUrl = 'https://cdn.example/old.glb?sig=expired';
    const newUrl = 'https://cdn.example/new.glb?sig=fresh';

    MockClient server({
      required Map<String, dynamic> task,
      List<String>? log,
      String? sameUrlForTask,
    }) {
      return MockClient((request) async {
        log?.add(request.url.toString());
        if (request.url.path.startsWith('/v3/tasks/')) return _json(task);
        if (request.url.toString() == oldUrl) return http.Response('gone', 403);
        return http.Response.bytes(_glb, 200);
      });
    }

    test('uses the known link when it still works, without asking Tripo',
        () async {
      final log = <String>[];
      final client = MockClient((request) async {
        log.add(request.url.toString());
        return http.Response.bytes(_glb, 200);
      });
      final path = '${tmp.path}/model.glb';

      await _service(client, _Clock())
          .downloadTaskModel('t1', path, knownUrl: newUrl);

      expect(log, [newUrl]);
      expect(File(path).readAsBytesSync(), _glb);
    });

    test('asks the task for a fresh link when the known one has expired',
        () async {
      final log = <String>[];
      final client = server(
        task: _task('success', progress: 100, output: {'model_url': newUrl}),
        log: log,
      );
      final path = '${tmp.path}/model.glb';

      await _service(client, _Clock())
          .downloadTaskModel('t1', path, knownUrl: oldUrl);

      expect(log, [oldUrl, '$_aiBase/tasks/t1', newUrl]);
      expect(File(path).readAsBytesSync(), _glb);
    });

    test('works from the task id alone', () async {
      final log = <String>[];
      final client = server(
        task: _task('success', progress: 100, output: {'model_url': newUrl}),
        log: log,
      );
      final path = '${tmp.path}/model.glb';

      await _service(client, _Clock()).downloadTaskModel('t1', path);

      expect(log, ['$_aiBase/tasks/t1', newUrl]);
      expect(File(path).existsSync(), isTrue);
    });

    test('says so while the model is still being generated', () async {
      final client = server(task: _task('running', progress: 40));

      await expectLater(
        _service(client, _Clock())
            .downloadTaskModel('t1', '${tmp.path}/model.glb'),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('still being generated'))
            .having((e) => e.message, 'message', contains('40%'))),
      );
    });

    test('reports a task that failed', () async {
      final client = server(
        task: _task('failed', errorCode: 2008, errorMessage: 'content policy'),
      );

      await expectLater(
        _service(client, _Clock())
            .downloadTaskModel('t1', '${tmp.path}/model.glb'),
        throwsA(isA<TripoException>()
            .having((e) => e.taskErrorCode, 'taskErrorCode', 2008)
            .having((e) => e.taskFailed, 'taskFailed', true)
            .having((e) => e.message, 'message', contains('content policy'))),
      );
    });

    test('does not hammer a dead link that the task hands back again',
        () async {
      final log = <String>[];
      final client = server(
        task: _task('success', progress: 100, output: {'model_url': oldUrl}),
        log: log,
      );

      await expectLater(
        _service(client, _Clock())
            .downloadTaskModel('t1', '${tmp.path}/model.glb', knownUrl: oldUrl),
        throwsA(isA<TripoException>().having((e) => e.httpStatus, 'status', 403)),
      );
      expect(log, [oldUrl, '$_aiBase/tasks/t1']);
    });
  });

  group('downloadModel', () {
    test('writes the GLB atomically and sends no Authorization header',
        () async {
      http.BaseRequest? seen;
      final client = MockClient((request) async {
        seen = request;
        return http.Response.bytes(_glb, 200);
      });
      final path = '${tmp.path}/model.glb';

      final file = await _service(client, _Clock())
          .downloadModel('https://cdn.example/model.glb?sig=abc', path);

      expect(file.path, path);
      expect(File(path).readAsBytesSync(), _glb);
      expect(File('$path.part').existsSync(), isFalse);
      expect(seen!.headers.containsKey('Authorization'), isFalse);
      expect(seen!.url.queryParameters['sig'], 'abc');
    });

    test('rejects a body that is not a GLB and leaves nothing behind',
        () async {
      final client = MockClient(
        (request) async => http.Response('<html>expired</html>', 200),
      );
      final path = '${tmp.path}/model.glb';

      await expectLater(
        _service(client, _Clock()).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('not a valid GLB'))),
      );
      expect(File(path).existsSync(), isFalse);
      expect(File('$path.part').existsSync(), isFalse);
    });

    test('rejects a GLB shorter than its own header declares', () async {
      // No Content-Length mismatch to catch it (the mock reports the length
      // it really sent): only the header length check can.
      final client = MockClient(
        (request) async => http.Response.bytes(_truncatedGlb, 200),
      );
      final path = '${tmp.path}/model.glb';

      await expectLater(
        _service(client, _Clock()).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('not a valid GLB'))),
      );
      expect(File(path).existsSync(), isFalse);
      expect(File('$path.part').existsSync(), isFalse);
    });

    test('keeps an existing good model when a new download fails', () async {
      final path = '${tmp.path}/model.glb';
      File(path).writeAsBytesSync(_glb);
      final client = MockClient((request) async => http.Response('nope', 403));

      await expectLater(
        _service(client, _Clock()).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>().having((e) => e.httpStatus, 'status', 403)),
      );
      expect(File(path).readAsBytesSync(), _glb);
    });

    test('does not retry a 403 but retries a 500', () async {
      var calls403 = 0;
      final forbidden = MockClient((request) async {
        calls403++;
        return http.Response('nope', 403);
      });
      await expectLater(
        _service(forbidden, _Clock())
            .downloadModel('https://cdn.example/m.glb', '${tmp.path}/a.glb'),
        throwsA(isA<TripoException>()),
      );
      expect(calls403, 1);

      var calls500 = 0;
      final flaky = MockClient((request) async {
        calls500++;
        if (calls500 == 1) return http.Response('oops', 500);
        return http.Response.bytes(_glb, 200);
      });
      final file = await _service(flaky, _Clock())
          .downloadModel('https://cdn.example/m.glb', '${tmp.path}/b.glb');
      expect(calls500, 2);
      expect(file.existsSync(), isTrue);
    });

    test('detects a truncated download, retries, and does not sleep after the last try',
        () async {
      var calls = 0;
      final client = MockClient.streaming((request, bodyStream) async {
        calls++;
        return http.StreamedResponse(
          Stream.value(_glb),
          200,
          contentLength: _glb.length + 100,
        );
      });
      final path = '${tmp.path}/model.glb';
      final clock = _Clock();

      await expectLater(
        _service(client, clock).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('interrupted'))),
      );
      expect(calls, 3);
      expect(clock.delays, [const Duration(seconds: 1), const Duration(seconds: 2)]);
      expect(File(path).existsSync(), isFalse);
      expect(File('$path.part').existsSync(), isFalse);
    });

    test('a content-encoded response is not judged by Content-Length',
        () async {
      // With transparent decompression Content-Length describes the compressed
      // bytes, so comparing it with what was written would reject good files.
      final client = MockClient.streaming((request, bodyStream) async {
        return http.StreamedResponse(
          Stream.value(_glb),
          200,
          contentLength: _glb.length + 50,
          headers: {'content-encoding': 'gzip'},
        );
      });
      final path = '${tmp.path}/model.glb';

      final file = await _service(client, _Clock())
          .downloadModel('https://cdn.example/m.glb', path);

      expect(file.readAsBytesSync(), _glb);
    });

    test('a stalled stream hits the per-chunk timeout instead of hanging',
        () async {
      var calls = 0;
      final controllers = <StreamController<List<int>>>[];
      addTearDown(() {
        for (final c in controllers) {
          c.close();
        }
      });
      final client = MockClient.streaming((request, bodyStream) async {
        calls++;
        final controller = StreamController<List<int>>()
          ..add(_glb.sublist(0, 4));
        controllers.add(controller);
        return http.StreamedResponse(controller.stream, 200);
      });
      final path = '${tmp.path}/model.glb';

      await expectLater(
        _service(
          client,
          _Clock(),
          downloadTimeout: const Duration(milliseconds: 50),
        ).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('Network error'))),
      );
      expect(calls, 3);
      expect(File('$path.part').existsSync(), isFalse);
    });

    test('a file system failure is reported and cleans up the .part file',
        () async {
      final client = MockClient(
        (request) async => http.Response.bytes(_glb, 200),
      );
      // The target is a directory, so the final rename cannot succeed.
      final path = '${tmp.path}/model.glb';
      Directory(path).createSync();

      await expectLater(
        _service(client, _Clock()).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('Could not save'))),
      );
      expect(File('$path.part').existsSync(), isFalse);
    });

    test('rejects a file that is only a GLB header', () async {
      final client = MockClient(
        (request) async => http.Response.bytes(_headerOnlyGlb, 200),
      );
      final path = '${tmp.path}/model.glb';

      await expectLater(
        _service(client, _Clock()).downloadModel('https://cdn.example/m.glb', path),
        throwsA(isA<TripoException>()
            .having((e) => e.message, 'message', contains('not a valid GLB'))),
      );
      expect(File(path).existsSync(), isFalse);
    });

    test('retries a rate-limited download', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        if (calls == 1) return http.Response('slow down', 429);
        return http.Response.bytes(_glb, 200);
      });
      final clock = _Clock();
      final path = '${tmp.path}/model.glb';

      final file = await _service(client, clock)
          .downloadModel('https://cdn.example/m.glb', path);

      expect(file.existsSync(), isTrue);
      expect(calls, 2);
      expect(clock.delays, [const Duration(seconds: 1)]);
    });

    test('refuses a non-http(s) URL such as a version string', () async {
      var calls = 0;
      final client = MockClient((request) async {
        calls++;
        return http.Response.bytes(_glb, 200);
      });

      await expectLater(
        _service(client, _Clock())
            .downloadModel('v3.1-20260211', '${tmp.path}/m.glb'),
        throwsA(isA<TripoException>()),
      );
      expect(calls, 0);
    });
  });
}
