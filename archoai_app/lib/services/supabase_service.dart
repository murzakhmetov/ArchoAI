import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path/path.dart' as p;
import 'package:supabase_flutter/supabase_flutter.dart';
import '../models/sensor_data.dart';
import '../models/artifact_model.dart';
import 'local_storage_service.dart';
import 'model_storage.dart';

class SupabaseService {
  static SupabaseService? _instance;
  SupabaseService._();

  static SupabaseService get instance {
    _instance ??= SupabaseService._();
    return _instance!;
  }

  SupabaseClient get _client => Supabase.instance.client;

  //  Auth 

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

  //  Sensor Data 

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

  /// With [sampleFallback] a sparse history (fewer than 10 rows) is replaced by
  /// generated sample points so the demo charts are never empty. Callers that
  /// feed the data to something that treats it as real (the AI chat) must pass
  /// false.
  Future<List<SensorData>> getSensorDataHistory({
    int hours = 24,
    bool sampleFallback = true,
  }) async {
    final since = DateTime.now().subtract(Duration(hours: hours));
    // PostgREST caps a response at ~1000 rows. Ask for the NEWEST rows and
    // flip them afterwards: an ascending query would return the oldest
    // 1000 rows of the window and the chart would stop in the past.
    final response = await _client
        .from('sensor_data')
        .select()
        .gte('created_at', since.toUtc().toIso8601String())
        .order('created_at', ascending: false)
        .limit(1000);

    final list = (response as List)
        .map((json) => SensorData.fromJson(json))
        .toList()
        .reversed
        .toList();
    
    // Fallback: if data is too sparse (less than 10 points), generated some points for a beautiful graph
    if (sampleFallback && list.length < 10) {
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

  //  Artifacts 

  /// With [seedIfEmpty] an empty collection is filled with the demo artifacts.
  /// Only the artifacts screen should do that: several screens load the list at
  /// startup and each of them would otherwise seed on its own.
  Future<List<ArtifactModel>> getArtifacts({bool seedIfEmpty = true}) async {
    _linkModelFilesOnce();
    final response = await _client
        .from('artifacts')
        .select()
        .order('created_at', ascending: false);
    var list = (response as List).map((json) => ArtifactModel.fromJson(json)).toList();
    
    // Auto-seed if empty
    if (list.isEmpty && isLoggedIn && seedIfEmpty) {
      await _seedIfStillEmpty();
      final seeded = await _client
          .from('artifacts')
          .select()
          .order('created_at', ascending: false);
      list = (seeded as List).map((json) => ArtifactModel.fromJson(json)).toList();
    }

    return _withLocalModelState(list);
  }

  /// Realtime list of artifacts, with the same per-device model state as
  /// [getArtifacts] applied to every emission.
  Stream<List<ArtifactModel>> streamArtifacts() {
    return _client
        .from('artifacts')
        .stream(primaryKey: ['id'])
        .order('created_at', ascending: false)
        .asyncMap((data) => _withLocalModelState(
              data.map((json) => ArtifactModel.fromJson(json)).toList(),
            ));
  }

  Future<void>? _seeding;

  // Concurrent callers share one seeding run, and the table is checked again
  // right before inserting, so the demo rows are never inserted twice.
  Future<void> _seedIfStillEmpty() {
    return _seeding ??= () async {
      try {
        final existing = await _client.from('artifacts').select('id').limit(1);
        if ((existing as List).isEmpty) await seedArtifacts();
      } finally {
        _seeding = null;
      }
    }();
  }

  /// Sets [ArtifactModel.localModelPath] from what is actually on this device.
  /// A path that came from the database belongs to another device or an old
  /// install, so it is never trusted.
  Future<List<ArtifactModel>> _withLocalModelState(
    List<ArtifactModel> artifacts,
  ) async {
    final taskIds = await LocalStorageService.instance.getTripoTaskIds();
    final result = <ArtifactModel>[];
    for (final artifact in artifacts) {
      if (await LocalStorageService.instance.modelExists(artifact.id)) {
        final localPath =
            await LocalStorageService.instance.getModelSavePath(artifact.id);
        result.add(artifact.copyWith(
          localModelPath: localPath,
          clearTripoTaskId: true,
        ));
      } else {
        // No model on this device: a stored Tripo task can still deliver it.
        result.add(artifact.copyWith(
          clearLocalModelPath: true,
          tripoTaskId: taskIds[artifact.id],
        ));
      }
    }
    return result;
  }

  Future<String> uploadArtifactImage(String artifactId, String filePath) async {
    final file = File(filePath);
    final extension = p.extension(filePath).replaceFirst('.', '').toLowerCase();
    final ext = extension.isEmpty ? 'jpg' : extension;
    final storagePath = 'artifacts/$artifactId.$ext';
    
    await _client.storage
        .from('artifact-images')
        .upload(storagePath, file, fileOptions: const FileOptions(upsert: true));
    
    final publicUrl = _client.storage
        .from('artifact-images')
        .getPublicUrl(storagePath);
    
    return publicUrl;
  }

  /// Stores a model file in the first Supabase model batch that has room (see
  /// supabase/migrations: models-01, models-02, ...). Buckets share the plan's
  /// storage quota, the batches only fill up in order. Returns the permanent
  /// public URL. Throws [ModelStorageException] with a readable message.
  Future<StoredModel> uploadArtifactModel(
    String artifactId,
    String filePath, {
    String? tripoTaskId,
  }) async {
    final file = File(filePath);
    final size = await file.length();
    if (size <= 0) throw ModelStorageException('The model file is empty.');
    if (size > maxModelBytes) {
      throw ModelStorageException('The model is larger than the 50 MB upload limit.');
    }

    final objectPath = '$artifactId.glb';

    Future<void> upload(String bucket) async {
      try {
        await _client.storage.from(bucket).upload(
              objectPath,
              file,
              fileOptions: FileOptions(
                // An upsert needs SELECT and UPDATE policies as well. The model batches have them
                // for signed-in users; the legacy bucket only accepts plain inserts.
                upsert: bucket != legacyModelBucket,
                contentType: 'model/gltf-binary',
              ),
            );
      } on StorageException catch (e) {
        throw ModelStorageException.fromStorage(e, bucket);
      }
    }

    // A regenerated model replaces the previous file in the SAME batch while that batch
    // still accepts it, so no orphan is left behind. If it refuses (the total cap is
    // reached or the batch was retired), the file goes to a batch picked now and the old
    // object stays until it is removed by hand.
    var bucket = await _bucketOfPreviousUpload(artifactId, objectPath);
    if (bucket != null) {
      try {
        await upload(bucket);
      } on ModelStorageException catch (e) {
        // Only a refusal (cap reached, batch retired) sends the file to another batch. Any other
        // failure is reported as it is, so a flaky connection does not leave stray copies behind.
        if (!e.refused) rethrow;
        bucket = null;
      }
    }
    if (bucket == null) {
      bucket = await _pickModelBucket(size);
      await upload(bucket);
    }

    final url = _client.storage.from(bucket).getPublicUrl(objectPath);

    // Recording the file is bookkeeping (usage per batch): its failure must not
    // fail an upload that already succeeded.
    String? fileId;
    try {
      final result = await _client.rpc('register_model_file', params: {
        'p_bucket_id': bucket,
        'p_object_path': objectPath,
        if (isDatabaseId(artifactId)) 'p_artifact_id': artifactId,
        'p_source': 'tripo',
        'p_tripo_task_id': ?tripoTaskId,
      });
      if (result is String && result.isNotEmpty) fileId = result;
    } catch (e) {
      debugPrint('register_model_file failed for $bucket/$objectPath: $e');
    }

    return StoredModel(
      bucketId: bucket,
      objectPath: objectPath,
      url: url,
      sizeBytes: size,
      fileId: fileId,
    );
  }

  /// Bucket that already holds this artifact's own `<id>.glb`, if any.
  Future<String?> _bucketOfPreviousUpload(String artifactId, String objectPath) async {
    if (!isDatabaseId(artifactId)) return null;
    try {
      final rows = await _client
          .from('model_files')
          .select('bucket_id')
          .eq('artifact_id', artifactId)
          .eq('object_path', objectPath)
          .order('created_at', ascending: false)
          .limit(1);
      if (rows.isEmpty) return null;
      final bucket = rows.first['bucket_id'];
      return bucket is String && bucket != legacyModelBucket ? bucket : null;
    } catch (e) {
      debugPrint('Could not look up the previous upload of $artifactId: $e');
      return null;
    }
  }

  bool _modelFilesLinked = false;

  /// Files that the Python rig stored are recorded as anonymous (anon cannot attach a
  /// file to an artifact). This links them to their artifacts, once per app session.
  /// Best effort: a failure only costs bookkeeping, never a model.
  Future<void> _linkModelFilesOnce() async {
    if (_modelFilesLinked || !isLoggedIn) return;
    _modelFilesLinked = true;
    try {
      await _client.rpc('link_model_files');
    } catch (e) {
      debugPrint('link_model_files failed: $e');
    }
  }

  Future<String> _pickModelBucket(int sizeBytes) async {
    try {
      final result =
          await _client.rpc('pick_model_bucket', params: {'p_size_bytes': sizeBytes});
      if (result is String && result.isNotEmpty) return result;
      throw ModelStorageException('Unexpected answer from pick_model_bucket.');
    } on PostgrestException catch (e) {
      // A project without the migrations still works through the legacy bucket.
      if (isMissingBatchFunction(e)) return legacyModelBucket;
      throw ModelStorageException.fromPostgrest(e);
    }
  }

  /// Points the artifact at its stored model. The Tripo task id is cleared: it
  /// is only needed until the model is stored. Throws when nothing was updated
  /// (the row is gone, or RLS refused), because an update that matches no row
  /// does not raise an error on its own.
  Future<void> attachModelToArtifact(String artifactId, StoredModel model) async {
    final rows = await _client
        .from('artifacts')
        .update({
          'model_url': model.url,
          'model_file_id': model.fileId,
          'tripo_task_id': null,
        })
        .eq('id', artifactId)
        .select('id');
    if (rows.isEmpty) {
      throw StateError('Artifact $artifactId was not updated (no access or deleted).');
    }
  }

  /// Stores (or clears, with null) the Tripo task of an artifact so a paid
  /// generation can be recovered from any device. Best effort for callers.
  Future<void> updateArtifactTaskId(String artifactId, String? taskId) async {
    await _client
        .from('artifacts')
        .update({'tripo_task_id': taskId})
        .eq('id', artifactId);
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

  // Models live in the Supabase batches (see uploadArtifactModel). Only their
  // permanent public URL (model_url) is written to artifacts, never the
  // temporary Tripo link and never the device-specific local file path.

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
