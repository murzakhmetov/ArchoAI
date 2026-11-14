import 'dart:convert';
import 'dart:io';

import 'package:archoai_app/models/artifact_model.dart';
import 'package:archoai_app/services/local_storage_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:path_provider/path_provider.dart';

const MethodChannel _pathChannel = MethodChannel('plugins.flutter.io/path_provider');

ArtifactModel _artifact(String id, {String name = 'Vase'}) => ArtifactModel(
      id: id,
      name: name,
      imagePath: '/images/$id.jpg',
      createdAt: DateTime.utc(2026, 1, 1),
    );

Map<String, dynamic> _record(String id) => _artifact(id).toJson();

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final service = LocalStorageService.instance;
  late Directory docs;

  File artifactsFile() => File('${docs.path}/artifacts.json');

  List<File> corruptBackups() => docs
      .listSync()
      .whereType<File>()
      .where((f) => f.path.contains('artifacts.json.corrupt-'))
      .toList();

  setUp(() async {
    docs = await Directory.systemTemp.createTemp('archoai_storage_test_');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
      _pathChannel,
      (MethodCall call) async =>
          call.method == 'getApplicationDocumentsDirectory' ? docs.path : null,
    );
    // Abort before any write if the mock is not honoured, so a test can never touch
    // the real documents folder.
    expect((await getApplicationDocumentsDirectory()).path, docs.path);
  });

  tearDown(() async {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(_pathChannel, null);
    await docs.delete(recursive: true);
  });

  group('Tripo task ids', () {
    File tasksFile() => File('${docs.path}/tripo_tasks.json');

    test('nothing stored means an empty map', () async {
      expect(await service.getTripoTaskIds(), isEmpty);
    });

    test('saves, reads and clears ids per artifact', () async {
      await service.saveTripoTaskId('a', 'task-a');
      await service.saveTripoTaskId('b', 'task-b');
      expect(await service.getTripoTaskIds(), {'a': 'task-a', 'b': 'task-b'});

      await service.saveTripoTaskId('a', 'task-a2'); // replaced, not duplicated
      await service.clearTripoTaskId('b');
      expect(await service.getTripoTaskIds(), {'a': 'task-a2'});
      expect(File('${docs.path}/tripo_tasks.json.tmp').existsSync(), isFalse);
    });

    test('clearing an unknown id writes nothing', () async {
      await service.clearTripoTaskId('nope');
      expect(tasksFile().existsSync(), isFalse);
    });

    test('a damaged file reads as empty and the next save repairs it', () async {
      tasksFile().writeAsStringSync('{not json');
      expect(await service.getTripoTaskIds(), isEmpty);

      await service.saveTripoTaskId('a', 'task-a');
      expect(await service.getTripoTaskIds(), {'a': 'task-a'});
    });

    test('bytes that are not valid UTF-8 read as empty and saving still works',
        () async {
      tasksFile().writeAsBytesSync([0xFF, 0xFE, 0x00, 0x80]);
      expect(await service.getTripoTaskIds(), isEmpty);

      await service.saveTripoTaskId('a', 'task-a');
      expect(await service.getTripoTaskIds(), {'a': 'task-a'});
      await service.clearTripoTaskId('a');
      expect(await service.getTripoTaskIds(), isEmpty);
    });

    test('entries that are not strings are ignored', () async {
      tasksFile().writeAsStringSync(jsonEncode({'a': 'task-a', 'b': 7, 'c': null}));
      expect(await service.getTripoTaskIds(), {'a': 'task-a'});
    });

    test('parallel saves with artifact writes lose nothing', () async {
      await Future.wait([
        service.saveTripoTaskId('a', 'task-a'),
        service.saveArtifact(_artifact('a')),
        service.saveTripoTaskId('b', 'task-b'),
        service.saveArtifact(_artifact('b')),
      ]);

      expect(await service.getTripoTaskIds(), {'a': 'task-a', 'b': 'task-b'});
      expect((await service.getArtifacts()).map((a) => a.id).toSet(), {'a', 'b'});
    });
  });

  group('artifacts CRUD', () {
    test('returns an empty list when nothing was saved', () async {
      expect(await service.getArtifacts(), isEmpty);
    });

    test('inserts new artifacts first and replaces an existing id in place', () async {
      await service.saveArtifact(_artifact('a'));
      await service.saveArtifact(_artifact('b'));
      expect((await service.getArtifacts()).map((a) => a.id), ['b', 'a']);

      await service.saveArtifact(_artifact('a', name: 'Renamed'));
      final artifacts = await service.getArtifacts();
      expect(artifacts.map((a) => a.id), ['b', 'a']);
      expect(artifacts.last.name, 'Renamed');
    });

    test('deleteArtifact removes only the given id', () async {
      await service.saveArtifact(_artifact('a'));
      await service.saveArtifact(_artifact('b'));
      await service.deleteArtifact('a');
      expect((await service.getArtifacts()).map((a) => a.id), ['b']);
    });

    test('writes valid JSON and leaves no temporary file behind', () async {
      await service.saveArtifact(_artifact('a'));

      final decoded = jsonDecode(artifactsFile().readAsStringSync()) as List<dynamic>;
      expect(decoded, hasLength(1));
      expect(File('${docs.path}/artifacts.json.tmp').existsSync(), isFalse);
    });

    test('model paths keep their behaviour', () async {
      final path = await service.getModelSavePath('abc');
      expect(path, startsWith(docs.path));
      expect(path, endsWith('/abc.glb'));
      expect(await service.modelExists('abc'), isFalse);

      File(path).writeAsBytesSync([1, 2, 3]);
      expect(await service.modelExists('abc'), isTrue);
    });
  });

  group('corrupt artifacts.json', () {
    test('invalid JSON: returns empty, keeps a copy, next save starts clean', () async {
      artifactsFile().writeAsStringSync('{"id": "a", "name": ');

      expect(await service.getArtifacts(), isEmpty);
      final backups = corruptBackups();
      expect(backups, hasLength(1));
      expect(backups.single.readAsStringSync(), '{"id": "a", "name": ');

      await service.saveArtifact(_artifact('b'));
      expect((await service.getArtifacts()).map((a) => a.id), ['b']);
      // The save read the same broken file again but must not add a second copy.
      expect(corruptBackups(), hasLength(1));
    });

    test('reading the same broken file again adds no copy, a different broken file does', () async {
      artifactsFile().writeAsStringSync('[1, 2');
      await service.getArtifacts();
      await service.getArtifacts();
      await service.getArtifacts();
      expect(corruptBackups(), hasLength(1));

      // Backup names carry the millisecond clock; a coarse OS clock tick must not
      // make the second name collide with the first.
      await Future<void>.delayed(const Duration(milliseconds: 50));
      artifactsFile().writeAsStringSync('[1, 2, 3');
      await service.getArtifacts();
      expect(corruptBackups(), hasLength(2));
    });

    test('invalid UTF-8 counts as corruption, not as an I/O error', () async {
      artifactsFile().writeAsBytesSync([0x5B, 0xFF, 0xFE, 0x5D]);

      expect(await service.getArtifacts(), isEmpty);
      expect(corruptBackups(), hasLength(1));
    });

    test('a root that is not a list counts as corruption', () async {
      artifactsFile().writeAsStringSync('{"artifacts": []}');

      expect(await service.getArtifacts(), isEmpty);
      expect(corruptBackups(), hasLength(1));
    });

    test('an empty file is not corruption and needs no backup', () async {
      artifactsFile().writeAsBytesSync([]);

      expect(await service.getArtifacts(), isEmpty);
      expect(corruptBackups(), isEmpty);
    });

    test('one bad record does not drop the others', () async {
      final original = jsonEncode([
        _record('a'),
        {'id': 'broken'}, // required fields are missing
        'not even an object',
        _record('c'),
      ]);
      artifactsFile().writeAsStringSync(original);

      expect((await service.getArtifacts()).map((a) => a.id), ['a', 'c']);
      expect(corruptBackups().single.readAsStringSync(), original);

      // Saving afterwards must keep the good records, and the one copy of the
      // original (with the bad records) stays the only backup.
      await service.saveArtifact(_artifact('d'));
      expect((await service.getArtifacts()).map((a) => a.id), ['d', 'a', 'c']);
      expect(corruptBackups().single.readAsStringSync(), original);
    });

    test('a healthy file is never backed up', () async {
      await service.saveArtifact(_artifact('a'));
      await service.getArtifacts();
      await service.saveArtifact(_artifact('b'));

      expect(corruptBackups(), isEmpty);
    });
  });

  group('write safety', () {
    test('writes are serialized: concurrent saves are all kept', () async {
      await Future.wait([
        for (var i = 0; i < 25; i++) service.saveArtifact(_artifact('id$i')),
      ]);

      final ids = (await service.getArtifacts()).map((a) => a.id).toSet();
      expect(ids, {for (var i = 0; i < 25; i++) 'id$i'});
    });

    test('mixed concurrent saves and deletes run in call order', () async {
      await service.saveArtifact(_artifact('old'));
      await Future.wait([
        service.saveArtifact(_artifact('a')),
        service.deleteArtifact('old'),
        service.saveArtifact(_artifact('b')),
        service.deleteArtifact('a'),
        service.saveArtifact(_artifact('c')),
      ]);

      expect((await service.getArtifacts()).map((a) => a.id), ['c', 'b']);
    });

    test('a failed write leaves the old file intact and does not block later writes', () async {
      await service.saveArtifact(_artifact('a'));
      final before = artifactsFile().readAsBytesSync();

      // A directory squatting on the temp path makes the write fail before the real
      // file is touched (a direct write to artifacts.json would not fail at all).
      final blocker = Directory('${docs.path}/artifacts.json.tmp')..createSync();
      await expectLater(service.saveArtifact(_artifact('b')), throwsA(isA<FileSystemException>()));
      expect(artifactsFile().readAsBytesSync(), before);

      blocker.deleteSync();
      await service.saveArtifact(_artifact('c'));
      expect((await service.getArtifacts()).map((a) => a.id), ['c', 'a']);
    });
  });
}
