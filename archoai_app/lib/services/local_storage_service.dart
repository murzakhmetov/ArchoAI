import 'dart:convert';
import 'dart:io';
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

  // ── Artifacts CRUD ──

  Future<List<ArtifactModel>> getArtifacts() async {
    try {
      final path = await _localPath;
      final file = File('$path/$_artifactsFileName');
      if (!await file.exists()) return [];

      final contents = await file.readAsString();
      final List<dynamic> jsonList = jsonDecode(contents);
      return jsonList.map((j) => ArtifactModel.fromJson(j)).toList();
    } catch (e) {
      return [];
    }
  }

  Future<void> saveArtifact(ArtifactModel artifact) async {
    final artifacts = await getArtifacts();
    final index = artifacts.indexWhere((a) => a.id == artifact.id);
    if (index >= 0) {
      artifacts[index] = artifact;
    } else {
      artifacts.insert(0, artifact);
    }
    await _saveArtifacts(artifacts);
  }

  Future<void> deleteArtifact(String id) async {
    final artifacts = await getArtifacts();
    artifacts.removeWhere((a) => a.id == id);
    await _saveArtifacts(artifacts);
  }

  Future<void> _saveArtifacts(List<ArtifactModel> artifacts) async {
    final path = await _localPath;
    final file = File('$path/$_artifactsFileName');
    final jsonList = artifacts.map((a) => a.toJson()).toList();
    await file.writeAsString(jsonEncode(jsonList));
  }

  // ── File Management ──

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
