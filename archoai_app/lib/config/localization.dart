import '../services/language_service.dart';

abstract class AppStrings {
  String get dashboard;
  String get artifacts;
  String get scanner;
  String get profile;
  
  String get temperature;
  String get humidity;
  String get airQuality;
  String get airTemperature;
  String get airHumidity;
  String get soilTemperature;
  String get soilMoisture;
  String get airSection;
  String get soilSection;
  String get noData;
  String get noSoilSensor;
  
  String get currentReading;
  String get optimalRange;
  String get aboveRecommended;
  String get belowRecommended;
  
  String get trend24h;
  String get safeZone;
  String get globalAiChat;
  String get askAi;
  String get dynamicInsight;
  
  String get artifactType;
  String get material;
  String get era;
  String get purpose;
  String get condition;
  String get cracks;
  String get location;
  String get schoolName;

  String get average;
  String get peak;
  String get lowest;

  String get account;
  String get settings;
  String get notifications;
  String get language;
  String get storage;
  String get about;
  String get version;
  String get terms;
  String get privacy;
  String get logout;
  String get logoutConfirm;
  String get cancel;
  String get researcher;

  String get addPhoto;
  String get photoUploading;
  String get photoSuccess;
  String get photoError;
  String get changePhoto;
  
  String get addArtifact;
  String get analyzing;
  String get analysisError;
  String get saveArtifact;

  String get server;
  String get serverCloud;
  String get serverPi;
  String get serverAuto;
  String serverAutoHint(String first, String second);
  String get serverChecking;
  String get serverOnline;
  String get serverOffline;
  String get serverInUse;
  String serverConnected(String name);
  String get serverSwitchFailed;
  String get serverAccountsNote;
  String get serverRetry;

  String translate(String value);
}

class RuStrings extends AppStrings {
  @override String get dashboard => 'Дашборд';
  @override String get artifacts => 'Артефакты';
  @override String get scanner => 'Сканер';
  @override String get profile => 'Профиль';
  
  @override String get temperature => 'Температура';
  @override String get humidity => 'Влажность';
  @override String get airQuality => 'Качество воздуха';
  @override String get airTemperature => 'Температура воздуха';
  @override String get airHumidity => 'Влажность воздуха';
  @override String get soilTemperature => 'Температура почвы';
  @override String get soilMoisture => 'Влажность почвы';
  @override String get airSection => 'Воздух';
  @override String get soilSection => 'Почва';
  @override String get noData => 'Нет данных';
  @override String get noSoilSensor => 'Датчик почвы не прислал данные';
  
  @override String get currentReading => 'ТЕКУЩИЙ ПОКАЗАТЕЛЬ';
  @override String get optimalRange => 'ОПТИМАЛЬНЫЙ ДИАПАЗОН';
  @override String get aboveRecommended => 'ВЫШЕ НОРМЫ';
  @override String get belowRecommended => 'НИЖЕ НОРМЫ';
  
  @override String get trend24h => 'ТРЕНД ЗА 24 ЧАСА';
  @override String get safeZone => 'ЗОНА СОХРАННОСТИ';
  @override String get globalAiChat => 'ЧАТ С ИИ-ЭКСПЕРТОМ';
  @override String get askAi => 'СПРОСИТЬ ИИ-СОВЕТНИКА';
  @override String get dynamicInsight => 'ДИНАМИЧЕСКИЙ АНАЛИЗ ИИ';
  
  @override String get artifactType => 'Тип';
  @override String get material => 'Материал';
  @override String get era => 'Эпоха';
  @override String get purpose => 'Назначение';
  @override String get condition => 'Состояние';
  @override String get cracks => 'Трещины';
  @override String get location => 'Местоположение';
  @override String get schoolName => 'НИШ ФМН г. Алматы';

  @override String get average => 'СРЕДНЕЕ';
  @override String get peak => 'ПИК';
  @override String get lowest => 'НИЗШЕЕ';

  @override String get account => 'АККАУНТ';
  @override String get settings => 'НАСТРОЙКИ';
  @override String get notifications => 'Уведомления';
  @override String get language => 'Язык';
  @override String get storage => 'Хранилище';
  @override String get about => 'О ПРИЛОЖЕНИИ';
  @override String get version => 'Версия';
  @override String get terms => 'Условия использования';
  @override String get privacy => 'Политика конфиденциальности';
  @override String get logout => 'ВЫЙТИ ИЗ СИСТЕМЫ';
  @override String get logoutConfirm => 'Вы уверены, что хотите выйти из системы?';
  @override String get cancel => 'Отмена';
  @override String get researcher => 'ИССЛЕДОВАТЕЛЬ';

  @override String get addPhoto => 'Добавить фото';
  @override String get photoUploading => 'Загрузка фото...';
  @override String get photoSuccess => 'Фото успешно загружено';
  @override String get photoError => 'Ошибка загрузки фото';
  @override String get changePhoto => 'Изменить фото';

  @override String get addArtifact => 'Новый артефакт';
  @override String get analyzing => 'ИИ анализирует артефакт...';
  @override String get analysisError => 'Ошибка ИИ при анализе';
  @override String get saveArtifact => 'Сохранить артефакт';

  @override String get server => 'Сервер';
  @override String get serverCloud => 'Облако Supabase';
  @override String get serverPi => 'Raspberry Pi';
  @override String get serverAuto => 'Авто';
  @override String serverAutoHint(String first, String second) => '$first, если доступен, иначе $second';
  @override String get serverChecking => 'Проверка...';
  @override String get serverOnline => 'На связи';
  @override String get serverOffline => 'Нет связи';
  @override String get serverInUse => 'Используется';
  @override String serverConnected(String name) => 'Подключено: $name';
  @override String get serverSwitchFailed => 'Не удалось подключиться к серверу';
  @override String get serverAccountsNote =>
      'У каждого сервера свои аккаунты. Вход на Raspberry Pi выполняет вход и в облако с тем же e-mail и паролем; на самом Pi войдите один раз.';
  @override String get serverRetry => 'Подключиться';

  @override
  String translate(String value) {
    final map = {
      'Ceramics': 'Керамика',
      'Clay': 'Глина',
      'Glass': 'Стекло',
      'Metal': 'Металл',
      'Gold': 'Золото',
      'Stone': 'Камень',
      'Unknown': 'Неизвестно',
      'Good': 'Хорошее',
      'Fair': 'Удовлетворительное',
      'Poor': 'Плохое',
      'Stable': 'Стабильное',
      'Broken': 'Повреждено',
      'Storage': 'Хранилище',
      'Exhibition': 'Выставка',
      'Restoration': 'Реставрация',
      'Camera': 'Камера',
      'Gallery': 'Галерея',
    };
    return map[value] ?? value;
  }
}

class EnStrings extends AppStrings {
  @override String get dashboard => 'Dashboard';
  @override String get artifacts => 'Artifacts';
  @override String get scanner => 'Scanner';
  @override String get profile => 'Profile';
  
  @override String get temperature => 'Temperature';
  @override String get humidity => 'Humidity';
  @override String get airQuality => 'Air Quality';
  @override String get airTemperature => 'Air Temperature';
  @override String get airHumidity => 'Air Humidity';
  @override String get soilTemperature => 'Soil Temperature';
  @override String get soilMoisture => 'Soil Moisture';
  @override String get airSection => 'Air';
  @override String get soilSection => 'Soil';
  @override String get noData => 'No data';
  @override String get noSoilSensor => 'The soil sensor has not sent data';
  
  @override String get currentReading => 'CURRENT READING';
  @override String get optimalRange => 'OPTIMAL RANGE';
  @override String get aboveRecommended => 'ABOVE NORMAL';
  @override String get belowRecommended => 'BELOW NORMAL';
  
  @override String get trend24h => '24H TREND';
  @override String get safeZone => 'PRESERVATION ZONE';
  @override String get globalAiChat => 'AI EXPERT CHAT';
  @override String get askAi => 'ASK AI ADVISOR';
  @override String get dynamicInsight => 'AI DYNAMIC ANALYSIS';
  
  @override String get artifactType => 'Type';
  @override String get material => 'Material';
  @override String get era => 'Era';
  @override String get purpose => 'Purpose';
  @override String get condition => 'Condition';
  @override String get cracks => 'Cracks';
  @override String get location => 'Location';
  @override String get schoolName => 'NIS PMN Almaty';

  @override String get average => 'AVERAGE';
  @override String get peak => 'PEAK';
  @override String get lowest => 'LOWEST';

  @override String get account => 'ACCOUNT';
  @override String get settings => 'SETTINGS';
  @override String get notifications => 'Notifications';
  @override String get language => 'Language';
  @override String get storage => 'Storage';
  @override String get about => 'ABOUT APP';
  @override String get version => 'Version';
  @override String get terms => 'Terms of Use';
  @override String get privacy => 'Privacy Policy';
  @override String get logout => 'SIGN OUT';
  @override String get logoutConfirm => 'Are you sure you want to sign out?';
  @override String get cancel => 'Cancel';
  @override String get researcher => 'RESEARCHER';

  @override String get addPhoto => 'Add Photo';
  @override String get photoUploading => 'Uploading photo...';
  @override String get photoSuccess => 'Photo uploaded successfully';
  @override String get photoError => 'Photo upload error';
  @override String get changePhoto => 'Change Photo';

  @override String get addArtifact => 'New Artifact';
  @override String get analyzing => 'AI analyzing artifact...';
  @override String get analysisError => 'AI analysis error';
  @override String get saveArtifact => 'Save Artifact';

  @override String get server => 'Server';
  @override String get serverCloud => 'Supabase Cloud';
  @override String get serverPi => 'Raspberry Pi';
  @override String get serverAuto => 'Auto';
  @override String serverAutoHint(String first, String second) => '$first when reachable, otherwise $second';
  @override String get serverChecking => 'Checking...';
  @override String get serverOnline => 'Online';
  @override String get serverOffline => 'Offline';
  @override String get serverInUse => 'In use';
  @override String serverConnected(String name) => 'Connected: $name';
  @override String get serverSwitchFailed => 'Could not connect to the server';
  @override String get serverAccountsNote =>
      'Each server has its own accounts. Signing in on the Raspberry Pi also signs you in on the cloud with the same e-mail and password; on the Pi itself, sign in once.';
  @override String get serverRetry => 'Connect';

  @override
  String translate(String value) => value; // Already English
}

class S {
  static AppStrings get current => LanguageService.instance.isRussian ? RuStrings() : EnStrings();
}

// Keep the old RU class as a bridge for now, but redirecting to S.current
class RU {
  static String get dashboard => S.current.dashboard;
  static String get artifacts => S.current.artifacts;
  static String get scanner => S.current.scanner;
  static String get profile => S.current.profile;
  static String get temperature => S.current.temperature;
  static String get humidity => S.current.humidity;
  static String get airQuality => S.current.airQuality;
  static String get airTemperature => S.current.airTemperature;
  static String get airHumidity => S.current.airHumidity;
  static String get soilTemperature => S.current.soilTemperature;
  static String get soilMoisture => S.current.soilMoisture;
  static String get currentReading => S.current.currentReading;
  static String get optimalRange => S.current.optimalRange;
  static String get aboveRecommended => S.current.aboveRecommended;
  static String get belowRecommended => S.current.belowRecommended;
  static String get trend24h => S.current.trend24h;
  static String get safeZone => S.current.safeZone;
  static String get globalAiChat => S.current.globalAiChat;
  static String get askAi => S.current.askAi;
  static String get dynamicInsight => S.current.dynamicInsight;
  static String get artifactType => S.current.artifactType;
  static String get material => S.current.material;
  static String get era => S.current.era;
  static String get purpose => S.current.purpose;
  static String get condition => S.current.condition;
  static String get cracks => S.current.cracks;
  static String get average => S.current.average;
  static String get peak => S.current.peak;
  static String get lowest => S.current.lowest;

  static String translate(String value) => S.current.translate(value);
}
