import 'dart:convert';
import 'dart:io';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:http/http.dart' as http;
import 'package:path/path.dart' as p;

class TripoService {
  static TripoService? _instance;
  TripoService._();

  static TripoService get instance {
    _instance ??= TripoService._();
    return _instance!;
  }

  static String _currentBaseUrl = 'https://api.tripo3d.ai/v2/openapi';
  static const String _fallbackBaseUrl = 'https://api.tripo3d.com/v2/openapi';

  String get _apiKey => dotenv.env['TRIPO_API_KEY'] ?? '';

  Map<String, String> get _headers => {
        'Authorization': 'Bearer $_apiKey',
      };

  Uri _getUri(String path) => Uri.parse('$_currentBaseUrl$path');

  Future<T> _withFallback<T>(Future<T> Function() action) async {
    try {
      return await action();
    } catch (e) {
      if (e is SocketException || e.toString().contains('Failed host lookup') || e.toString().contains('Network is unreachable')) {
        _currentBaseUrl = _fallbackBaseUrl;
        return await action();
      }
      rethrow;
    }
  }

  /// Upload image using multipart/form-data and get a file_token
  Future<String> uploadImage(File imageFile) async {
    return _withFallback(() async {
      final request = http.MultipartRequest('POST', _getUri('/upload'));
      request.headers.addAll(_headers);
      
      request.files.add(await http.MultipartFile.fromPath(
        'file', 
        imageFile.path,
      ));

      final streamedResponse = await request.send();
      final response = await http.Response.fromStream(streamedResponse);

      if (response.statusCode != 200) {
        throw TripoException('Upload failed (${response.statusCode}): ${response.body}');
      }

      final data = jsonDecode(response.body);
      if (data['code'] != 0) {
        throw TripoException(data['message'] ?? 'Upload error');
      }

      return (data['data']['image_token'] ?? data['data']['file_token']) as String;
    });
  }

  /// Create an image-to-model task using a file_token
  Future<String> createImageToModelTask(File imageFile) async {
    // 1. Upload first
    final token = await uploadImage(imageFile);
    final extension = p.extension(imageFile.path).replaceFirst('.', '').toLowerCase();
    final type = (extension == 'jpg' || extension == 'jpeg') ? 'jpg' : 'png';

    // 2. Create task
    return _withFallback(() async {
      final response = await http.post(
        _getUri('/task'),
        headers: {
          ..._headers,
          'Content-Type': 'application/json',
        },
        body: jsonEncode({
          'type': 'image_to_model',
          'file': {
            'type': type,
            'file_token': token,
          },
        }),
      );

      if (response.statusCode != 200) {
        throw TripoException('Task creation failed: ${response.body}');
      }

      final data = jsonDecode(response.body);
      if (data['code'] != 0) {
        throw TripoException(data['message'] ?? 'Task creation error');
      }

      return data['data']['task_id'] as String;
    });
  }

  /// Poll task status
  Future<TripoTaskResult> getTaskStatus(String taskId) async {
    return _withFallback(() async {
      final response = await http.get(
        _getUri('/task/$taskId'),
        headers: _headers,
      );

      if (response.statusCode != 200) {
        throw TripoException('Failed to get task: ${response.body}');
      }

      final data = jsonDecode(response.body);
      if (data['code'] != 0) {
        throw TripoException(data['message'] ?? 'Task status error');
      }

      final taskData = data['data'];

      // Recursive search for model URL
      String? modelUrl;
      void findModel(dynamic obj) {
        if (obj is Map) {
          modelUrl ??= obj['model'] ?? obj['model_url'] ?? obj['glb'] ?? obj['pbr_model'] ?? obj['result_url'];
          if (modelUrl == null) {
            for (var v in obj.values) {
              findModel(v);
            }
          }
        } else if (obj is List) {
          for (var item in obj) {
            findModel(item);
          }
        }
      }
      findModel(taskData);

      return TripoTaskResult(
        taskId: taskId,
        status: (taskData['status'] as String? ?? 'unknown').toLowerCase(),
        progress: (taskData['progress'] as num?)?.toInt() ?? 0,
        modelUrl: modelUrl,
        renderedImageUrl: taskData['output']?['rendered_image'] as String?,
      );
    });
  }

  /// Wait for task completion with polling
  Future<TripoTaskResult> waitForTask(
    String taskId, {
    Duration pollInterval = const Duration(seconds: 4),
    Duration timeout = const Duration(minutes: 10),
    Function(int progress)? onProgress,
  }) async {
    final deadline = DateTime.now().add(timeout);

    while (DateTime.now().isBefore(deadline)) {
      final result = await getTaskStatus(taskId);

      if (result.status == 'success') {
        if (result.modelUrl != null) {
          return result;
        } else if (result.progress >= 100) {
          // Extra wait for model processing lag
          await Future.delayed(const Duration(seconds: 2));
          continue;
        }
      }

      if (result.status == 'failed' || result.status == 'cancelled' || result.status == 'banned') {
        throw TripoException('Task failed: ${result.status}');
      }

      onProgress?.call(result.progress);
      await Future.delayed(pollInterval);
    }

    throw TripoException('Task timed out after ${timeout.inMinutes} minutes');
  }

  /// Download model file
  Future<File> downloadModel(String modelUrl, String savePath) async {
    final response = await http.get(Uri.parse(modelUrl));
    if (response.statusCode != 200) {
      throw TripoException('Failed to download model');
    }

    final file = File(savePath);
    await file.writeAsBytes(response.bodyBytes);
    return file;
  }
}

class TripoTaskResult {
  final String taskId;
  final String status;
  final int progress;
  final String? modelUrl;
  final String? renderedImageUrl;

  TripoTaskResult({
    required this.taskId,
    required this.status,
    required this.progress,
    this.modelUrl,
    this.renderedImageUrl,
  });

  bool get isComplete => status == 'success' && modelUrl != null;
  bool get isFailed => status == 'failed' || status == 'cancelled' || status == 'banned';
}

class TripoException implements Exception {
  final String message;
  TripoException(this.message);

  @override
  String toString() => message;
}
