import 'package:archoai_app/services/model_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

void main() {
  group('ModelStorageException.fromPostgrest', () {
    test('53100 means the storage is full', () {
      final e = ModelStorageException.fromPostgrest(
        PostgrestException(message: 'storage_full: every model batch is full', code: '53100'),
      );

      expect(e.code, '53100');
      expect(e.message, contains('storage is full'));
      expect(e.toString(), e.message);
    });

    test('54000 means the file is too large', () {
      final e = ModelStorageException.fromPostgrest(
        PostgrestException(message: 'model file is larger than', code: '54000'),
      );

      expect(e.message, contains('upload limit'));
    });

    test('anything else keeps the server message', () {
      final e = ModelStorageException.fromPostgrest(
        PostgrestException(message: 'boom', code: 'XX000'),
      );

      expect(e.message, contains('boom'));
      expect(e.code, 'XX000');
    });
  });

  group('ModelStorageException.fromStorage', () {
    test('a 403 or an RLS refusal means full or not allowed', () {
      final byStatus = ModelStorageException.fromStorage(
        const StorageException('Unauthorized', statusCode: '403'),
        'models-01',
      );
      final byText = ModelStorageException.fromStorage(
        const StorageException('new row violates row-level security policy'),
        'models-02',
      );

      expect(byStatus.message, contains('models-01'));
      expect(byStatus.message, contains('storage is full'));
      expect(byText.message, contains('storage is full'));
      expect(byStatus.refused, isTrue);
      expect(byText.refused, isTrue);
    });

    test('413 and 415 get their own messages', () {
      expect(
        ModelStorageException.fromStorage(
          const StorageException('Payload too large', statusCode: '413'),
          'models-01',
        ).message,
        contains('upload limit'),
      );
      expect(
        ModelStorageException.fromStorage(
          const StorageException('mime type text/plain is not supported', statusCode: '415'),
          'models-01',
        ).message,
        contains('file type'),
      );
      expect(
        ModelStorageException.fromStorage(
          const StorageException('Payload too large', statusCode: '413'),
          'models-01',
        ).refused,
        isFalse,
      );
    });

    test('a missing bucket is a refusal, so another batch can take the file', () {
      final e = ModelStorageException.fromStorage(
        const StorageException('Bucket not found', statusCode: '404'),
        'models-02',
      );

      expect(e.refused, isTrue);
      expect(e.message, contains('models-02'));
    });

    test('anything else keeps the server message', () {
      final e = ModelStorageException.fromStorage(
        const StorageException('socket closed', statusCode: '500'),
        'models-03',
      );

      expect(e.message, contains('models-03'));
      expect(e.message, contains('socket closed'));
      expect(e.refused, isFalse);
    });
  });

  group('isMissingBatchFunction', () {
    test('a database without the migrations falls back to the legacy bucket', () {
      expect(isMissingBatchFunction(PostgrestException(message: 'x', code: 'PGRST202')), isTrue);
      expect(isMissingBatchFunction(PostgrestException(message: 'x', code: '42883')), isTrue);
    });

    test('real failures are not mistaken for a missing function', () {
      expect(isMissingBatchFunction(PostgrestException(message: 'x', code: '53100')), isFalse);
      expect(isMissingBatchFunction(PostgrestException(message: 'x')), isFalse);
    });
  });

  group('isDatabaseId', () {
    test('accepts uuids in any letter case', () {
      expect(isDatabaseId('c4917c7b-162c-4204-8a73-69cb5f09b573'), isTrue);
      expect(isDatabaseId('C4917C7B-162C-4204-8A73-69CB5F09B573'), isTrue);
    });

    test('rejects device-only ids and garbage', () {
      expect(isDatabaseId('1794591744123'), isFalse); // scanner timestamp id
      expect(isDatabaseId(''), isFalse);
      expect(isDatabaseId('c4917c7b-162c-4204-8a73-69cb5f09b57'), isFalse);
      expect(isDatabaseId('c4917c7b-162c-4204-8a73-69cb5f09b573x'), isFalse);
    });
  });

  test('limits match the Pi server and the database', () {
    expect(maxModelBytes, 209715200);
    expect(legacyModelBucket, '3d1');
  });
}
