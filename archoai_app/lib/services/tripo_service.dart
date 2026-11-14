import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart' show MediaType;
import 'package:path/path.dart' as p;

/// Client for the Tripo 3D generation API, version 3.
///
/// v3 replaced the single `POST /v2/openapi/task` endpoint (with a `type`
/// field) by one endpoint per task type, renamed several fields and made the
/// `model` version a required parameter. Flow used by the app:
///
///   1. `POST /v3/files`                      -> `file_token`
///   2. `POST /v3/generation/image-to-model`  -> `task_id`
///   3. `GET  /v3/tasks/{task_id}`            -> poll until `success`
///   4. download `output.model_url`           -> GLB on disk
///
/// Output URLs are signed and temporary: download the GLB right after the task
/// succeeds and never keep the Tripo URL as a permanent link.
///
/// Tripo accounts (and their API keys) belong to one region, so the host is
/// chosen explicitly and never switched automatically: a global key does not
/// work on the China host.
///
/// Configuration, read from `.env` or `--dart-define`:
///   * `TRIPO_API_KEY`    API key (Bearer token).
///   * `TRIPO_REGION`     `global` (default) or `china` / `cn`.
///   * `TRIPO_BASE_URL`   full base URL override, wins over `TRIPO_REGION`.
///   * `TRIPO_MODEL`      geometry model, default [defaultModel].
///   * `TRIPO_FACE_LIMIT` positive integer polycount cap; smaller GLB files
///                        load faster in the viewer.
class TripoService {
  TripoService({
    required String apiKey,
    String baseUrl = globalBaseUrl,
    this.model = defaultModel,
    this.faceLimit,
    http.Client? client,
    this.requestTimeout = const Duration(seconds: 30),
    this.uploadTimeout = const Duration(seconds: 120),
    this.downloadTimeout = const Duration(seconds: 60),
    Future<void> Function(Duration)? delay,
    DateTime Function()? now,
  })  : _apiKey = apiKey.trim(),
        _baseUrl = _normalizeBaseUrl(baseUrl),
        _client = client ?? http.Client(),
        _delay = delay ?? ((d) => Future<void>.delayed(d)),
        _now = now ?? DateTime.now;

  /// Builds the service from `.env` / `--dart-define` values.
  factory TripoService.fromEnv({http.Client? client}) {
    String? env(String key) {
      if (!dotenv.isInitialized) return null;
      final value = dotenv.env[key]?.trim();
      return (value == null || value.isEmpty) ? null : value;
    }

    var regionBaseUrl = globalBaseUrl;
    final region = env('TRIPO_REGION')?.toLowerCase();
    if (region == 'china' || region == 'cn') {
      regionBaseUrl = chinaBaseUrl;
    } else if (region != null && region != 'global') {
      debugPrint('Tripo: unknown TRIPO_REGION "$region", using the global region.');
    }

    final faceLimit = int.tryParse(env('TRIPO_FACE_LIMIT') ?? '');
    return TripoService(
      apiKey: env('TRIPO_API_KEY') ??
          const String.fromEnvironment('TRIPO_API_KEY'),
      baseUrl: env('TRIPO_BASE_URL') ?? regionBaseUrl,
      model: env('TRIPO_MODEL') ?? defaultModel,
      faceLimit: (faceLimit != null && faceLimit > 0) ? faceLimit : null,
      client: client,
    );
  }

  static TripoService? _instance;

  static TripoService get instance => _instance ??= TripoService.fromEnv();

  /// Latest recommended Tripo H-series model at the time of the v3 migration.
  static const String defaultModel = 'v3.1-20260211';

  /// Global region (default) and China region. Same API, separate accounts.
  static const String globalBaseUrl = 'https://openapi.tripo3d.ai/v3';
  static const String chinaBaseUrl = 'https://openapi.tripo3d.com/v3';

  static const int _maxImageBytes = 20 * 1024 * 1024;
  static const int _maxAttempts = 3;

  /// Total time one request may spend sleeping between retries.
  static const Duration _maxRetryWait = Duration(seconds: 60);
  static const Map<String, String> _imageMimeTypes = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
  };

  final String model;
  final int? faceLimit;
  final Duration requestTimeout;
  final Duration uploadTimeout;

  /// Maximum silence between two chunks while downloading a model.
  final Duration downloadTimeout;

  final String _apiKey;
  final String _baseUrl;
  final http.Client _client;
  final Future<void> Function(Duration) _delay;
  final DateTime Function() _now;

  String get baseUrl => _baseUrl;

  static String _normalizeBaseUrl(String url) {
    final cleaned = url.trim().replaceAll(RegExp(r'/+$'), '');
    return cleaned.isEmpty ? globalBaseUrl : cleaned;
  }

  Map<String, String> get _authHeaders => {'Authorization': 'Bearer $_apiKey'};

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  /// Uploads a JPEG/PNG photo and returns its `file_token`.
  Future<String> uploadImage(File imageFile) async {
    final extension =
        p.extension(imageFile.path).replaceFirst('.', '').toLowerCase();
    final mimeType = _imageMimeTypes[extension];
    if (mimeType == null) {
      throw TripoException(
        'Unsupported image format ".$extension". Use a JPEG or PNG photo.',
      );
    }
    final length = await imageFile.length();
    if (length == 0) {
      throw TripoException('The selected image file is empty.');
    }
    if (length > _maxImageBytes) {
      throw TripoException('The image is larger than 20 MB.');
    }

    final response = await _send(
      '/files',
      (uri) async {
        final request = http.MultipartRequest('POST', uri)
          ..headers.addAll(_authHeaders);
        request.files.add(await http.MultipartFile.fromPath(
          'file',
          imageFile.path,
          contentType: MediaType.parse(mimeType),
        ));
        return request;
      },
      timeout: uploadTimeout,
      idempotent: true,
    );

    final data = _unwrap(response)['data'];
    final token = data is Map ? data['file_token'] : null;
    if (token is! String || token.isEmpty) {
      throw TripoException('Tripo did not return a file token for the upload.');
    }
    return token;
  }

  /// Uploads [imageFile] and starts an image-to-model task. Returns the task id.
  Future<String> createImageToModelTask(File imageFile) async {
    final token = await uploadImage(imageFile);
    return createImageToModelTaskFromToken(token);
  }

  /// Starts an image-to-model task for an already uploaded image.
  ///
  /// Creating a task costs credits and is not idempotent, so this request is
  /// retried only when Tripo answers 429 (nothing was created). Timeouts and
  /// dropped connections are reported instead of blindly re-sent.
  Future<String> createImageToModelTaskFromToken(String fileToken) async {
    final payload = <String, Object>{
      'input': fileToken,
      'model': model,
      'face_limit': ?faceLimit,
    };

    final response = await _send(
      '/generation/image-to-model',
      (uri) async => http.Request('POST', uri)
        ..headers.addAll({
          ..._authHeaders,
          'Content-Type': 'application/json',
        })
        ..body = jsonEncode(payload),
      timeout: requestTimeout,
      idempotent: false,
    );

    final Map<String, dynamic> envelope;
    try {
      envelope = _unwrap(response);
    } on TripoException catch (e) {
      throw _warnIfMaybeCreated(e);
    }
    final data = envelope['data'];
    final taskId = data is Map ? data['task_id'] : null;
    if (taskId is! String || taskId.isEmpty) {
      throw _warnIfMaybeCreated(
        TripoException('Tripo did not return a task id.'),
      );
    }
    return taskId;
  }

  /// A server failure or a malformed answer to the paid create call says
  /// nothing about whether the task was created (and billed), so the user is
  /// told to check before trying again. An explicit rejection (4xx, or an
  /// error code in the envelope) means nothing was created.
  static TripoException _warnIfMaybeCreated(TripoException e) {
    final status = e.httpStatus;
    final serverFailed = status != null && status >= 500;
    final rejected =
        !serverFailed && ((status != null && status >= 400) || e.code != null);
    if (rejected) return e;
    return TripoException(
      '${e.message}. The task may still have been created, so check your '
      'Tripo balance before trying again.',
      code: e.code,
      httpStatus: status,
      requestId: e.requestId,
      suggestion: e.suggestion,
      transient: e.transient,
      cause: e.cause,
    );
  }

  /// Reads the current state of a task.
  Future<TripoTaskResult> getTaskStatus(String taskId) async {
    final response = await _send(
      '/tasks/${Uri.encodeComponent(taskId)}',
      (uri) async => http.Request('GET', uri)..headers.addAll(_authHeaders),
      timeout: requestTimeout,
      idempotent: true,
    );

    final data = _unwrap(response)['data'];
    if (data is! Map) {
      throw TripoException('Unexpected task response from Tripo.');
    }
    return TripoTaskResult.fromJson(taskId, Map<String, dynamic>.from(data));
  }

  /// Polls a task until it succeeds with a model URL.
  ///
  /// Network trouble while polling is tolerated: the wait continues until the
  /// deadline, and only gives up when no poll has succeeded for [maxOutage].
  ///
  /// Throws [TripoException] when the task fails, is cancelled, expires, or
  /// when [timeout] is exceeded. Throws [TripoCancelledException] when
  /// [isCancelled] turns true (for example the screen was closed).
  Future<TripoTaskResult> waitForTask(
    String taskId, {
    Duration pollInterval = const Duration(seconds: 2),
    Duration timeout = const Duration(minutes: 15),
    Duration maxOutage = const Duration(minutes: 3),
    void Function(int progress)? onProgress,
    bool Function()? isCancelled,
  }) async {
    final startedAt = _now();
    final deadline = startedAt.add(timeout);
    var lastSuccessfulPoll = startedAt;
    var successWithoutUrl = 0;
    var loggedUnknownStatus = false;

    while (true) {
      if (isCancelled?.call() ?? false) {
        throw TripoCancelledException();
      }
      if (!_now().isBefore(deadline)) {
        throw TripoException(
          'The 3D model is taking too long (over ${timeout.inMinutes} min). '
          'Try again later.',
        );
      }

      final TripoTaskResult result;
      try {
        result = await getTaskStatus(taskId);
        lastSuccessfulPoll = _now();
      } on TripoException catch (e) {
        // Phone networks come and go: keep trying until the outage is long.
        if (!e.isTransient || _now().difference(lastSuccessfulPoll) > maxOutage) {
          rethrow;
        }
        await _delay(pollInterval);
        continue;
      }

      if (result.isSuccess) {
        if (result.modelUrl != null) return result;
        // The task is done but the output is not visible yet: allow a few
        // more polls, then give up instead of spinning until the timeout.
        if (++successWithoutUrl > 3) {
          throw TripoException('Tripo finished the task but returned no model.');
        }
      } else if (result.isFailed) {
        throw TripoException(
          _taskFailureMessage(result),
          taskErrorCode: result.errorCode,
          taskFailed: true,
        );
      } else {
        if (!result.isKnownActive && !loggedUnknownStatus) {
          loggedUnknownStatus = true;
          debugPrint(
            'Tripo: unrecognised status "${result.status}" for task $taskId, '
            'still waiting.',
          );
        }
        onProgress?.call(result.progress);
      }

      await _delay(pollInterval);
    }
  }

  /// Downloads a GLB model to [savePath].
  ///
  /// The data is streamed into `<savePath>.part`, validated (GLB header and
  /// the length it declares) and then renamed, so an interrupted download
  /// never leaves a truncated file that the app would treat as a saved model.
  /// The signed URL is fetched without the API key.
  Future<File> downloadModel(String modelUrl, String savePath) async {
    final uri = Uri.tryParse(modelUrl);
    if (uri == null ||
        !(uri.scheme == 'http' || uri.scheme == 'https') ||
        uri.host.isEmpty) {
      throw TripoException('Tripo returned an invalid model URL.');
    }

    final partFile = File('$savePath.part');
    TripoException? lastError;

    for (var attempt = 1; attempt <= _maxAttempts; attempt++) {
      try {
        final streamed = await _client
            .send(http.Request('GET', uri))
            .timeout(requestTimeout);

        if (streamed.statusCode != 200) {
          // Drain so the connection can be reused/closed cleanly.
          await streamed.stream.drain<void>().catchError((_) {});
          throw TripoException(
            'Model download failed (HTTP ${streamed.statusCode}). '
            'The download link may have expired.',
            httpStatus: streamed.statusCode,
            transient: streamed.statusCode >= 500 || streamed.statusCode == 429,
          );
        }

        var written = 0;
        final sink = partFile.openWrite();
        try {
          await for (final chunk in streamed.stream.timeout(downloadTimeout)) {
            sink.add(chunk);
            written += chunk.length;
          }
          await sink.flush();
        } finally {
          await sink.close();
        }

        final expected = streamed.contentLength;
        final encoded = streamed.headers['content-encoding'];
        if (expected != null &&
            (encoded == null || encoded.isEmpty) &&
            written != expected) {
          throw TripoException(
            'Model download was interrupted.',
            transient: true,
          );
        }
        if (!await _isCompleteGlb(partFile)) {
          throw TripoException(
            'The downloaded file is not a valid GLB model (it may be incomplete).',
          );
        }

        await partFile.rename(savePath);
        return File(savePath);
      } on TripoException catch (e) {
        lastError = e;
        if (!e.isTransient) break;
      } on FileSystemException catch (e) {
        await _deleteQuietly(partFile);
        throw TripoException('Could not save the model file: ${e.message}');
      } catch (e) {
        if (!_isNetworkError(e)) rethrow;
        lastError = TripoException(
          'Network error while downloading the model.',
          transient: true,
          cause: e,
        );
      }

      if (attempt < _maxAttempts) await _delay(_backoff(attempt));
    }

    await _deleteQuietly(partFile);
    throw lastError ?? TripoException('Model download failed.');
  }

  /// Saves the model of a finished task to [savePath].
  ///
  /// Tries [knownUrl] first when there is one. Signed links expire, and Tripo
  /// recommends asking the task again for a fresh link, so when the known link
  /// fails (or is missing) the task is queried and its current link is used.
  /// This is what lets a paid generation be recovered later from the task id
  /// alone, for example after the app was closed while it was running.
  Future<File> downloadTaskModel(
    String taskId,
    String savePath, {
    String? knownUrl,
  }) async {
    TripoException? knownUrlError;
    if (knownUrl != null) {
      try {
        return await downloadModel(knownUrl, savePath);
      } on TripoException catch (e) {
        knownUrlError = e;
      }
    }

    final task = await getTaskStatus(taskId);
    if (task.isFailed) {
      throw TripoException(
        _taskFailureMessage(task),
        taskErrorCode: task.errorCode,
        taskFailed: true,
      );
    }
    final freshUrl = task.modelUrl;
    if (!task.isSuccess || freshUrl == null) {
      throw TripoException(
        task.isKnownActive
            ? 'The 3D model is still being generated (${task.progress}%). '
                'Try again in a minute.'
            : 'The 3D task has no model yet. Try again in a minute.',
        transient: true,
      );
    }
    // Same link again would fail the same way: report the original problem.
    if (freshUrl == knownUrl && knownUrlError != null) throw knownUrlError;
    return downloadModel(freshUrl, savePath);
  }

  // ---------------------------------------------------------------------------
  // Transport
  // ---------------------------------------------------------------------------

  /// Sends one logical request with timeout and retry.
  ///
  /// * 429 is retried for every request (Tripo did not process it); 5xx and
  ///   network errors only when [idempotent]. Waits honour `Retry-After` but
  ///   never exceed [_maxRetryWait] in total.
  /// * The host is fixed: a task id exists only on the region that created it.
  Future<http.Response> _send(
    String path,
    Future<http.BaseRequest> Function(Uri uri) build, {
    required Duration timeout,
    required bool idempotent,
  }) async {
    if (_apiKey.isEmpty) {
      throw TripoException(
        'TRIPO_API_KEY is not set. Add it to the .env file.',
        code: 2,
      );
    }

    final uri = Uri.parse('$_baseUrl$path');
    var attempt = 0;
    var waited = Duration.zero;

    while (true) {
      attempt++;
      try {
        final request = await build(uri);
        final streamed = await _client.send(request).timeout(timeout);
        final response = await http.Response.fromStream(streamed).timeout(
          timeout,
        );

        final status = response.statusCode;
        final retryable = status == 429 || (status >= 500 && idempotent);
        if (retryable && attempt < _maxAttempts) {
          final wait = _retryDelay(response, attempt);
          if (waited + wait <= _maxRetryWait) {
            waited += wait;
            await _delay(wait);
            continue;
          }
        }
        return response;
      } catch (e) {
        if (!_isNetworkError(e)) rethrow;

        if (idempotent && attempt < _maxAttempts) {
          await _delay(_backoff(attempt));
          continue;
        }

        // A failure while connecting means nothing reached Tripo, so even for
        // the paid create call there is no risk that a task exists.
        final neverSent = idempotent || _isConnectFailure(e);
        throw TripoException(
          neverSent
              ? 'Could not reach Tripo. Check your internet connection.'
              : 'The connection dropped while creating the 3D task. The task '
                  'may still have been created, so check your Tripo balance '
                  'before trying again.',
          transient: true,
          cause: e,
        );
      }
    }
  }

  /// What the server asks for (`Retry-After`), but never faster than the
  /// backoff step: `Retry-After: 0` must not turn into an instant retry loop.
  Duration _retryDelay(http.Response response, int attempt) {
    final backoff = _backoff(attempt);
    final seconds = int.tryParse(response.headers['retry-after']?.trim() ?? '');
    if (seconds == null || seconds < 0) return backoff;
    final asked = Duration(seconds: seconds);
    return asked > backoff ? asked : backoff;
  }

  /// 1 s, 2 s, 4 s ... capped at 32 s.
  Duration _backoff(int attempt) =>
      Duration(seconds: math.min(32, 1 << (attempt - 1)));

  static bool _isNetworkError(Object e) =>
      e is SocketException ||
      e is TlsException ||
      e is TimeoutException ||
      e is http.ClientException;

  /// True only for failures that happen before any byte of the request could
  /// have reached Tripo: DNS, no route, refused connection, failed TLS
  /// handshake. Other TLS errors can strike after the request went out.
  static bool _isConnectFailure(Object e) {
    if (e is HandshakeException) return true;
    if (e is! SocketException) return false;
    final text = '${e.message} ${e.osError?.message ?? ''}'.toLowerCase();
    return text.contains('failed host lookup') ||
        text.contains('no address associated') ||
        text.contains('nodename nor servname') ||
        text.contains('network is unreachable') ||
        text.contains('connection refused');
  }

  /// Decodes the `{code, data}` envelope and throws on any API error.
  Map<String, dynamic> _unwrap(http.Response response) {
    Map<String, dynamic>? body;
    try {
      final decoded = jsonDecode(utf8.decode(response.bodyBytes));
      if (decoded is Map) body = Map<String, dynamic>.from(decoded);
    } catch (_) {
      // Non-JSON body (for example an HTML error page from a proxy).
    }

    final code = body?['code'];
    final ok = response.statusCode >= 200 &&
        response.statusCode < 300 &&
        code is num &&
        code == 0;
    if (ok) return body!;

    throw TripoException.fromApiError(
      httpStatus: response.statusCode,
      body: body,
    );
  }

  static String _taskFailureMessage(TripoTaskResult result) {
    final reason = result.errorMessage ??
        (result.errorCode != null ? 'error ${result.errorCode}' : null);
    final label = result.status == 'cancelled'
        ? 'The 3D task was cancelled'
        : 'The 3D task failed';
    return reason == null ? label : '$label: $reason';
  }

  /// GLB header: magic `glTF`, version, total length (little-endian uint32).
  /// Rejects non-GLB bodies and bodies shorter than the header declares.
  static Future<bool> _isCompleteGlb(File file) async {
    final handle = await file.open();
    try {
      final actual = await handle.length();
      // 12-byte header plus at least one 8-byte chunk header.
      if (actual < 20) return false;
      final header = await handle.read(12);
      if (header.length < 12) return false;

      final magicOk = header[0] == 0x67 && // g
          header[1] == 0x6C && // l
          header[2] == 0x54 && // T
          header[3] == 0x46; // F
      if (!magicOk) return false;

      final declared = header[8] |
          (header[9] << 8) |
          (header[10] << 16) |
          (header[11] << 24);
      return declared <= actual;
    } finally {
      await handle.close();
    }
  }

  static Future<void> _deleteQuietly(File file) async {
    try {
      if (await file.exists()) await file.delete();
    } catch (_) {}
  }
}

/// Snapshot of a Tripo task as returned by `GET /v3/tasks/{task_id}`.
class TripoTaskResult {
  TripoTaskResult({
    required this.taskId,
    required this.status,
    required this.progress,
    this.modelUrl,
    this.renderedImageUrl,
    this.errorCode,
    this.errorMessage,
  });

  /// Reads `data` of a task response. Only `data.output` is searched for URLs.
  factory TripoTaskResult.fromJson(String taskId, Map<String, dynamic> data) {
    final output = data['output'];
    final outputMap = output is Map
        ? Map<String, dynamic>.from(output)
        : const <String, dynamic>{};
    final rawStatus = data['status'];
    final rawProgress = data['progress'];
    final errorCode = data['error_code'];
    final errorMessage = data['error_message'];
    final status = rawStatus is String ? rawStatus.trim().toLowerCase() : '';

    return TripoTaskResult(
      taskId: taskId,
      status: status.isEmpty ? 'unknown' : status,
      progress: rawProgress is num ? rawProgress.toInt().clamp(0, 100) : 0,
      modelUrl: extractModelUrl(outputMap),
      renderedImageUrl: _firstHttpUrl(
        outputMap,
        const ['rendered_image_url', 'rendered_image'],
      ),
      errorCode: errorCode is num ? errorCode.toInt() : null,
      errorMessage: errorMessage is String && errorMessage.isNotEmpty
          ? errorMessage
          : null,
    );
  }

  final String taskId;

  /// Lower-cased API status: queued, running, success, failed, cancelled,
  /// legacy banned/expired, or anything unrecognised ('unknown').
  final String status;
  final int progress;
  final String? modelUrl;
  final String? renderedImageUrl;

  /// Task-level failure code (`error_code`). Not the same namespace as the
  /// request-level codes of [TripoException.code].
  final int? errorCode;
  final String? errorMessage;

  static const Set<String> _failureStatuses = {
    'failed',
    'cancelled',
    'banned',
    'expired',
  };

  bool get isSuccess => status == 'success';

  bool get isComplete => isSuccess && modelUrl != null;

  /// Terminal failure. Unrecognised statuses are deliberately not failures.
  bool get isFailed => _failureStatuses.contains(status);

  bool get isKnownActive => status == 'queued' || status == 'running';

  /// Keys of `data.output` that can hold the GLB, best first. `model_url` is
  /// the v3 name; the others are legacy v2 names. Values must be http(s) URLs
  /// so version strings such as `v3.1-20260211` are never mistaken for a link.
  static const List<String> _modelUrlKeys = [
    'model_url',
    'pbr_model',
    'model',
    'base_model',
  ];

  static String? extractModelUrl(Object? output) {
    if (output is! Map) return null;
    return _firstHttpUrl(Map<String, dynamic>.from(output), _modelUrlKeys);
  }

  static String? _firstHttpUrl(Map<String, dynamic> map, List<String> keys) {
    for (final key in keys) {
      final value = map[key];
      if (value is String) {
        final uri = Uri.tryParse(value);
        if (uri != null &&
            (uri.scheme == 'http' || uri.scheme == 'https') &&
            uri.host.isNotEmpty) {
          return value;
        }
      }
    }
    return null;
  }
}

class TripoException implements Exception {
  TripoException(
    this.message, {
    this.code,
    this.taskErrorCode,
    this.taskFailed = false,
    this.httpStatus,
    this.requestId,
    this.suggestion,
    this.transient = false,
    this.cause,
  });

  /// Builds an exception from a Tripo error envelope
  /// `{code, status: "error", message, suggestion, request_id}`.
  factory TripoException.fromApiError({
    required int httpStatus,
    Map<String, dynamic>? body,
  }) {
    final rawCode = body?['code'];
    final code = rawCode is num ? rawCode.toInt() : null;
    final apiMessage = body?['message'];
    final suggestion = body?['suggestion'];
    final requestId = body?['request_id'];
    final detail = apiMessage is String && apiMessage.isNotEmpty
        ? apiMessage
        : 'HTTP $httpStatus';
    final hint = _hintFor(code, httpStatus);

    return TripoException(
      hint == null ? 'Tripo error: $detail' : '$hint ($detail)',
      code: code,
      httpStatus: httpStatus,
      requestId: requestId is String ? requestId : null,
      suggestion: suggestion is String ? suggestion : null,
      // A 2xx page without the {code, data} envelope (a proxy or maintenance
      // page) says nothing about the task, so asking again is reasonable.
      transient: httpStatus == 429 ||
          httpStatus >= 500 ||
          (httpStatus >= 200 && httpStatus < 300 && code == null),
    );
  }

  final String message;

  /// Request-level Tripo error code (for example 2010 = insufficient credits).
  final int? code;

  /// `error_code` of a task that failed after it was accepted. Kept apart from
  /// [code]: the same number can mean something else in the two places.
  final int? taskErrorCode;

  /// The task itself ended in a terminal failure (failed, cancelled, banned,
  /// expired): its id is useless from now on and nothing can be recovered.
  final bool taskFailed;
  final int? httpStatus;
  final String? requestId;
  final String? suggestion;

  /// True when retrying the same call later can reasonably succeed.
  final bool transient;
  final Object? cause;

  bool get isTransient => transient;
  bool get isAuthError => code == 2 || code == 1002 || httpStatus == 401;
  bool get isInsufficientCredits => code == 2010;

  static String? _hintFor(int? code, int httpStatus) {
    switch (code) {
      case 2:
      case 1002:
        return 'Invalid or missing Tripo API key (check TRIPO_REGION too)';
      case 2010:
        return 'Not enough Tripo credits';
      case 2008:
        return 'Tripo rejected the image (content policy)';
      case 2003:
      case 2004:
        return 'Tripo could not read the image (empty or unsupported file)';
      case 2015:
        return 'This Tripo model version is no longer supported. '
            'Update TRIPO_MODEL';
      case 2018:
        return 'The model is too complex for Tripo';
      case 1004:
        return 'Tripo rejected the request parameters';
      case 1007:
      case 2000:
        return 'Tripo is busy (rate or concurrency limit), try again soon';
    }
    if (httpStatus == 401) {
      return 'Invalid or missing Tripo API key (check TRIPO_REGION too)';
    }
    if (httpStatus == 429) {
      return 'Tripo is busy (rate or concurrency limit), try again soon';
    }
    if (httpStatus >= 500) return 'Tripo server error';
    return null;
  }

  @override
  String toString() => message;
}

/// Thrown by [TripoService.waitForTask] when the caller asked to stop.
class TripoCancelledException extends TripoException {
  TripoCancelledException() : super('Cancelled');
}
