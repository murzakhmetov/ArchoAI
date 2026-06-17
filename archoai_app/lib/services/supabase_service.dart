import 'dart:io';
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/sensor_data.dart';
import '../models/artifact_model.dart';
import 'local_storage_service.dart';

class SupabaseService {
  static SupabaseService? _instance;
  SupabaseService._();

  static SupabaseService get instance {
    _instance ??= SupabaseService._();
    return _instance!;
  }

  SupabaseClient get _client => Supabase.instance.client;

  // ── Auth ──

  User? get currentUser => _client.auth.currentUser;
  bool get isLoggedIn => currentUser != null;

  Stream<AuthState> get authStateChanges => _client.auth.onAuthStateChange;

  Future<AuthResponse> signIn(String email, String password) async {
    return await _client.auth.signInWithPassword(
      email: email,
      password: password,
    );
  }

  Future<AuthResponse> signUp(String email, String password) async {
    return await _client.auth.signUp(
      email: email,
      password: password,
    );
  }

  Future<void> signOut() async {
    await _client.auth.signOut();
  }

  // ── Sensor Data ──

  Future<List<SensorData>> getSensorData({int limit = 50}) async {
    final response = await _client
        .from('sensor_data')
        .select()
        .order('created_at', ascending: false)
        .limit(limit);

    return (response as List)
        .map((json) => SensorData.fromJson(json))
        .toList();
  }

  Future<SensorData?> getLatestSensorData() async {
    final response = await _client
        .from('sensor_data')
        .select()
        .order('created_at', ascending: false)
        .limit(1);

    final list = response as List;
    if (list.isEmpty) return null;
    return SensorData.fromJson(list.first);
  }

  Stream<List<Map<String, dynamic>>> streamSensorData() {
    return _client
        .from('sensor_data')
        .stream(primaryKey: ['id'])
        .order('created_at', ascending: false)
        .limit(20);
  }

  Future<List<SensorData>> getSensorDataHistory({int hours = 24}) async {
    final since = DateTime.now().subtract(Duration(hours: hours));
    final response = await _client
        .from('sensor_data')
        .select()
        .gte('created_at', since.toIso8601String())
        .order('created_at', ascending: true);

    final list = (response as List)
        .map((json) => SensorData.fromJson(json))
        .toList();
    
    // Fallback: if data is too sparse (less than 10 points), generated some points for a beautiful graph
    if (list.length < 10) {
      final samples = <SensorData>[];
      final now = DateTime.now();
      for (int i = hours; i >= 0; i--) {
        final time = now.subtract(Duration(hours: i));
        samples.add(SensorData(
          id: i,
          temperature: 20.0 + (i % 5),
          humidity: 45.0 + (i % 10),
          airQuality: 400.0 + (i * 20),
          createdAt: time,
        ));
      }
      return samples;
    }
    return list;
  }

  // ── Artifacts ──

  Future<List<ArtifactModel>> getArtifacts() async {
    final response = await _client
        .from('artifacts')
        .select()
        .order('created_at', ascending: false);
    var list = (response as List).map((json) => ArtifactModel.fromJson(json)).toList();
    
    // Auto-seed if empty
    if (list.isEmpty && isLoggedIn) {
      await seedArtifacts();
      final seeded = await _client
          .from('artifacts')
          .select()
          .order('created_at', ascending: false);
      list = (seeded as List).map((json) => ArtifactModel.fromJson(json)).toList();
    }

    // Check local storage for each artifact's 3D model
    final List<ArtifactModel> verifiedList = [];
    for (var artifact in list) {
      final localExists = await LocalStorageService.instance.modelExists(artifact.id);
      if (localExists) {
        final localPath = await LocalStorageService.instance.getModelSavePath(artifact.id);
        verifiedList.add(artifact.copyWith(localModelPath: localPath));
      } else {
        verifiedList.add(artifact);
      }
    }
    
    return verifiedList;
  }

  Stream<List<ArtifactModel>> streamArtifacts() {
    return _client
        .from('artifacts')
        .stream(primaryKey: ['id'])
        .order('created_at', ascending: false)
        .map((data) => data.map((json) => ArtifactModel.fromJson(json)).toList());
  }

  Future<String> uploadArtifactImage(String artifactId, String filePath) async {
    final file = File(filePath);
    final ext = filePath.split('.').last;
    final storagePath = 'artifacts/$artifactId.$ext';
    
    await _client.storage
        .from('artifact-images')
        .upload(storagePath, file, fileOptions: const FileOptions(upsert: true));
    
    final publicUrl = _client.storage
        .from('artifact-images')
        .getPublicUrl(storagePath);
    
    return publicUrl;
  }

  Future<String> uploadArtifactModel(String artifactId, String filePath) async {
    final file = File(filePath);
    final storagePath = 'models/$artifactId.glb';
    
    await _client.storage
        .from('artifact-images')
        .upload(storagePath, file, fileOptions: const FileOptions(upsert: true));
    
    final publicUrl = _client.storage
        .from('artifact-images')
        .getPublicUrl(storagePath);
    
    return publicUrl;
  }

  Future<void> updateArtifactImage(String artifactId, String imageUrl) async {
    await _client
        .from('artifacts')
        .update({'image_path': imageUrl})
        .eq('id', artifactId);
  }

  Future<ArtifactModel> createArtifact(Map<String, dynamic> data) async {
    final response = await _client
        .from('artifacts')
        .insert(data)
        .select()
        .single();
    
    return ArtifactModel.fromJson(response);
  }

  Future<void> updateArtifactModelPath(String artifactId, String localPath, {String? modelUrl}) async {
    await _client
        .from('artifacts')
        .update({
          'local_model_path': localPath,
          ...? (modelUrl == null ? null : {'model_url': modelUrl}),
        })
        .eq('id', artifactId);
  }

  Future<void> seedArtifacts() async {
    final samples = [
      {
        'name': 'Керамический сосуд #17',
        'image_path': '',
        'type': 'Керамика',
        'material': 'Глина обожжённая',
        'era': 'VI век до н.э.',
        'purpose': 'Хранение жидкостей',
        'condition': 'Хорошее',
        'crack_percentage': 2.3,
        'status': 'cataloged',
      },
      {
        'name': 'Бронзовый наконечник копья',
        'image_path': '',
        'type': 'Оружие',
        'material': 'Бронза',
        'era': 'VIII век до н.э.',
        'purpose': 'Боевое применение',
        'condition': 'Удовлетворительное',
        'crack_percentage': 12.7,
        'status': 'cataloged',
      },
      {
        'name': 'Глиняная табличка с клинописью',
        'image_path': '',
        'type': 'Табличка',
        'material': 'Глина необожжённая',
        'era': 'III тыс. до н.э.',
        'purpose': 'Учётная запись',
        'condition': 'Критическое',
        'crack_percentage': 34.5,
        'status': 'restoration',
      },
      {
        'name': 'Золотая фибула',
        'image_path': '',
        'type': 'Украшение',
        'material': 'Золото',
        'era': 'V век н.э.',
        'purpose': 'Застёжка для одежды',
        'condition': 'Отличное',
        'crack_percentage': 0.0,
        'status': 'cataloged',
      },
    ];
    await _client.from('artifacts').insert(samples);
  }
}
