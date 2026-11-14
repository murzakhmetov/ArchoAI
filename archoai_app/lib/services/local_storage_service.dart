import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';
import '../models/artifact_model.dart';

class LocalStorageService {
  static LocalStorageService? _instance;
  LocalStorageService._();

  static LocalStorageService get instance {
    _instance ??= LocalStorageService._();
    return _instance!;
  }

  static const String _artifactsFileName = 'artifacts.json';
  static const String _tripoTasksFileName = 'tripo_tasks.json';

  // Tail of the queue that serializes artifact writes, see _serialized.
  Future<void> _writeQueue = Future<void>.value();

  // The last broken file copied aside. It stays broken until a save rewrites it, and
  // every read in between would otherwise add another identical copy.
  (String, List<int>)? _lastBackup;

  Future<String> get _localPath async {
    final directory = await getApplicationDocumentsDirectory();
    return directory.path;
  }

  Future<String> get _modelsPath async {
    final path = await _localPath;
    final dir = Directory('$path/models');
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    return dir.path;
  }

  Future<String> get _imagesPath async {
    final path = await _localPath;
    final dir = Directory('$path/images');
    if (!await dir.exists()) {
      await dir.create(recursive: true);
    }
    return dir.path;
  }

  //  Artifacts CRUD 

  Future<List<ArtifactModel>> getArtifacts() async {
    try {
      return await _readArtifacts();
    } catch (e) {
      debugPrint('LocalStorageService: could not read artifacts: $e');
      return [];
    }
  }

  Future<void> saveArtifact(ArtifactModel artifact) {
    return _serialized(() async {
      final artifacts = await _readArtifacts();
      final index = artifacts.indexWhere((a) => a.id == artifact.id);
      if (index >= 0) {
        artifacts[index] = artifact;
      } else {
        artifacts.insert(0, artifact);
      }
      await _saveArtifacts(artifacts);
    });
  }

  Future<void> deleteArtifact(String id) {
    return _serialized(() async {
      final artifacts = await _readArtifacts();
      artifacts.removeWhere((a) => a.id == id);
      await _saveArtifacts(artifacts);
    });
  }

  // Runs write operations one after another. Each save/delete reads the whole file
  // and writes it back, so two overlapping calls would lose one of the updates.
  Future<void> _serialized(Future<void> Function() action) {
    final run = _writeQueue.then((_) => action());
    // The queue must outlive a failed write, otherwise every later write would hang.
    _writeQueue = run.then<void>((_) {}, onError: (Object _) {});
    return run;
  }

  // Parse problems are recovered from: the broken file is copied aside and unreadable
  // records are skipped. I/O errors are rethrown, so a failed read is never mistaken
  // for "no artifacts" by a caller that is about to write the file back.
  Future<List<ArtifactModel>> _readArtifacts() async {
    final path = await _localPath;
    final file = File('$path/$_artifactsFileName');
    if (!await file.exists()) return [];

    final bytes = await file.readAsBytes();
    if (bytes.isEmpty) return [];

    final records = _decodeRecords(bytes);
    if (records == null) {
      await _backupCorruptFile(file, bytes);
      return [];
    }

    final artifacts = <ArtifactModel>[];
    var skipped = 0;
    for (final record in records) {
      try {
        artifacts.add(ArtifactModel.fromJson(record as Map<String, dynamic>));
      } catch (e) {
        skipped++;
        debugPrint('LocalStorageService: skipping unreadable artifact record: $e');
      }
    }
    // The next save rewrites the file without the skipped records, so keep a copy.
    if (skipped > 0) await _backupCorruptFile(file, bytes);
    return artifacts;
  }

  // Null when the bytes are not valid UTF-8 / JSON or the top level is not a list.
  List<dynamic>? _decodeRecords(List<int> bytes) {
    try {
      final decoded = jsonDecode(utf8.decode(bytes));
      if (decoded is List) return decoded;
      debugPrint('LocalStorageService: artifacts file does not contain a list');
    } on FormatException catch (e) {
      debugPrint('LocalStorageService: artifacts file is not valid JSON: $e');
    }
    return null;
  }

  // Best effort: a failed backup must not stop the app from carrying on.
  Future<void> _backupCorruptFile(File file, List<int> bytes) async {
    final last = _lastBackup;
    if (last != null && last.$1 == file.path && listEquals(last.$2, bytes)) return;
    try {
      final backupPath = '${file.path}.corrupt-${DateTime.now().millisecondsSinceEpoch}';
      await file.copy(backupPath);
      _lastBackup = (file.path, bytes);
      debugPrint('LocalStorageService: copied unreadable artifacts file to $backupPath');
    } catch (e) {
      debugPrint('LocalStorageService: could not back up unreadable artifacts file: $e');
    }
  }

  Future<void> _saveArtifacts(List<ArtifactModel> artifacts) {
    return _writeJsonAtomically(
      _artifactsFileName,
      artifacts.map((a) => a.toJson()).toList(),
    );
  }

  Future<void> _writeJsonAtomically(String fileName, Object json) async {
    final path = await _localPath;
    final file = File('$path/$fileName');
    // Write beside the target and rename over it: a crash mid-write then leaves the
    // old file intact instead of a truncated one.
    final tmp = File('${file.path}.tmp');
    await tmp.writeAsString(jsonEncode(json), flush: true);
    await tmp.rename(file.path);
  }

  //  Tripo tasks

  // A Tripo generation is paid for the moment the task is created, but its result can
  // only be fetched while the app is still around. The task id of every artifact whose
  // model is not saved on this device yet is kept here, so the model can be recovered
  // later (a fresh download link is requested from the task) instead of being paid for
  // again after a failed download, a timeout or a restart.

  Future<Map<String, String>> getTripoTaskIds() async {
    try {
      return await _readTripoTasks();
    } catch (e) {
      debugPrint('LocalStorageService: could not read Tripo task ids: $e');
      return {};
    }
  }

  Future<void> saveTripoTaskId(String artifactId, String taskId) {
    return _serialized(() async {
      final tasks = await _readTripoTasks();
      tasks[artifactId] = taskId;
      await _writeJsonAtomically(_tripoTasksFileName, tasks);
    });
  }

  Future<void> clearTripoTaskId(String artifactId) {
    return _serialized(() async {
      final tasks = await _readTripoTasks();
      if (tasks.remove(artifactId) == null) return;
      await _writeJsonAtomically(_tripoTasksFileName, tasks);
    });
  }

  // A damaged file only costs the ability to resume; the next save rewrites it.
  Future<Map<String, String>> _readTripoTasks() async {
    final path = await _localPath;
    final file = File('$path/$_tripoTasksFileName');
    if (!await file.exists()) return {};
    try {
      // utf8.decode throws a FormatException on malformed bytes (readAsString
      // would throw a FileSystemException that no later save could recover from).
      final decoded = jsonDecode(utf8.decode(await file.readAsBytes()));
      if (decoded is Map) {
        return {
          for (final entry in decoded.entries)
            if (entry.key is String && entry.value is String)
              entry.key as String: entry.value as String,
        };
      }
    } on FormatException catch (e) {
      debugPrint('LocalStorageService: Tripo task file is not valid JSON: $e');
    }
    return {};
  }

  //  File Management 

  Future<String> saveImage(File imageFile, String artifactId) async {
    final imagesDir = await _imagesPath;
    final extension = imageFile.path.split('.').last;
    final savedPath = '$imagesDir/$artifactId.$extension';
    await imageFile.copy(savedPath);
    return savedPath;
  }

  Future<String> getModelSavePath(String artifactId) async {
    final modelsDir = await _modelsPath;
    return '$modelsDir/$artifactId.glb';
  }

  Future<bool> modelExists(String artifactId) async {
    final path = await getModelSavePath(artifactId);
    return File(path).exists();
  }

  Future<void> deleteFiles(String artifactId) async {
    final modelsDir = await _modelsPath;
    final imagesDir = await _imagesPath;

    final modelFile = File('$modelsDir/$artifactId.glb');
    if (await modelFile.exists()) await modelFile.delete();

    final dir = Directory(imagesDir);
    if (await dir.exists()) {
      await for (final entity in dir.list()) {
        if (entity.path.contains(artifactId)) {
          await entity.delete();
        }
      }
    }
  }
}
