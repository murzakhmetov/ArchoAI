# archoai_app

Flutter client of ArchoAI: artifact catalogue with AI analysis (Gemini), 3D scanning (Tripo API v3), live storage-room monitoring (Supabase realtime) and an AI conservator chat.

## Run

```bash
cp .env.example .env     # then fill in your keys
flutter pub get
flutter run
```

`.env` is git-ignored but declared as a Flutter asset, so `flutter run`, `flutter build` and `flutter test` need the file to exist. If a key is missing or Supabase cannot be initialised, the app shows a configuration screen instead of crashing.

## Test

```bash
flutter test
flutter analyze
```

On Windows, native-asset hooks fail with `Failed to canonicalize path ... (122)` when the project lives under a path longer than 260 characters or one containing non-ASCII characters. Keep the checkout in a short ASCII path such as `C:\dev\ArchoAI` when running `flutter test`.

## Configuration

See `.env.example` for every variable. The Tripo integration (`lib/services/tripo_service.dart`) is described in the repository root README.
