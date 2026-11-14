import 'package:supabase_flutter/supabase_flutter.dart';

/// Bucket used before the batched model storage existed. It is the fallback
/// for a database that does not have the `pick_model_bucket` function yet.
const String legacyModelBucket = '3d1';

/// Free plan limit for one uploaded file.
const int maxModelBytes = 50 * 1024 * 1024;

/// A model file that was stored in one of the Supabase model batches.
class StoredModel {
  const StoredModel({
    required this.bucketId,
    required this.objectPath,
    required this.url,
    required this.sizeBytes,
    this.fileId,
  });

  final String bucketId;
  final String objectPath;

  /// Permanent public URL of the GLB.
  final String url;
  final int sizeBytes;

  /// Row id in `model_files`, or null when recording the file failed (the
  /// upload itself still succeeded).
  final String? fileId;
}

/// A readable failure of the cloud model storage. Local files are never
/// affected by it: callers keep the model on the device and show [message].
class ModelStorageException implements Exception {
  ModelStorageException(this.message, {this.code, this.refused = false});

  /// Maps the errors of the `pick_model_bucket` RPC (see supabase/migrations).
  factory ModelStorageException.fromPostgrest(PostgrestException e) {
    switch (e.code) {
      case '53100':
        return ModelStorageException(
          'The cloud storage is full (free plan quota).',
          code: e.code,
        );
      case '54000':
        return ModelStorageException(
          'The model is larger than the 50 MB upload limit.',
          code: e.code,
        );
      default:
        return ModelStorageException(
          'Could not choose a cloud storage batch: ${e.message}',
          code: e.code,
        );
    }
  }

  /// Maps a refused Storage upload. A 403 on a model batch means the policy
  /// said no: the total cap is reached or the file is not an allowed .glb.
  factory ModelStorageException.fromStorage(StorageException e, String bucket) {
    final text = e.message.toLowerCase();
    final status = e.statusCode;
    if (status == '403' || text.contains('row-level security')) {
      return ModelStorageException(
        'Upload to $bucket was refused: the cloud storage is full or the file is not allowed.',
        code: status,
        refused: true,
      );
    }
    if (status == '413' || text.contains('too large') || text.contains('exceeded')) {
      return ModelStorageException(
        'The model is larger than the 50 MB upload limit.',
        code: status,
      );
    }
    if (status == '415' || text.contains('mime')) {
      return ModelStorageException(
        'The cloud storage does not accept this file type.',
        code: status,
      );
    }
    if (status == '404' || text.contains('bucket not found')) {
      return ModelStorageException(
        'Upload to $bucket failed: that storage batch does not exist.',
        code: status,
        refused: true,
      );
    }
    return ModelStorageException('Upload to $bucket failed: ${e.message}', code: status);
  }

  final String message;
  final String? code;

  /// True when the Storage policies refused the upload (the total cap is reached or the batch was
  /// retired). Only then can another batch accept the file; any other failure is not worth a second
  /// try elsewhere.
  final bool refused;

  @override
  String toString() => message;
}

/// True when the database does not know the batching functions yet (an old
/// project without the migrations), so the legacy bucket should be used.
bool isMissingBatchFunction(PostgrestException e) =>
    e.code == 'PGRST202' || e.code == '42883';

final RegExp _uuidPattern = RegExp(
  r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
);

/// Only real database ids can be linked to a `model_files` row; scanner
/// artifacts that live on the device have timestamp ids.
bool isDatabaseId(String id) => _uuidPattern.hasMatch(id);
