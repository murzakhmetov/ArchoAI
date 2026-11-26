import 'package:flutter/material.dart';

import '../config/app_colors.dart';
import '../config/localization.dart';
import '../services/backend_config.dart';
import '../services/backend_service.dart';

/// "Supabase Cloud" or "Raspberry Pi".
String serverName(BackendEndpoint endpoint) =>
    endpoint.kind == BackendKind.cloud ? S.current.serverCloud : S.current.serverPi;

/// "Raspberry Pi · 192.168.1.61" style label of [endpoint].
String serverLabel(BackendEndpoint endpoint) =>
    endpoint.kind == BackendKind.cloud ? serverName(endpoint) : '${serverName(endpoint)} · ${endpoint.host}';

/// Lets the user pick Auto or one of the configured servers, then reconnects.
/// After a successful switch the whole app is rebuilt on the new server, so
/// [context] may no longer be mounted when this returns.
Future<void> showServerPicker(BuildContext context) async {
  final choice = await showModalBottomSheet<ServerChoice>(
    context: context,
    backgroundColor: AppColors.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (_) => const _ServerPickerSheet(),
  );
  // Picking the current choice again is allowed: Auto then asks both servers
  // again and may move back to the first one.
  if (choice == null || !context.mounted) return;

  showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (_) => const Center(child: CircularProgressIndicator(color: AppColors.cyan)),
  );
  final error = await BackendService.instance.switchTo(choice);
  // On success the old tree (and this dialog) is gone; the same server keeps it.
  if (!context.mounted) return;
  Navigator.of(context, rootNavigator: true).pop();
  if (error != null) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text('${S.current.serverSwitchFailed}: $error'),
      backgroundColor: AppColors.red.withValues(alpha: 0.9),
    ));
  }
}

class _ServerPickerSheet extends StatefulWidget {
  const _ServerPickerSheet();

  @override
  State<_ServerPickerSheet> createState() => _ServerPickerSheetState();
}

class _ServerPickerSheetState extends State<_ServerPickerSheet> {
  late final Map<ServerChoice, Future<bool>> _reachable;

  @override
  void initState() {
    super.initState();
    final endpoints = BackendService.instance.config?.endpoints ?? const <BackendEndpoint>[];
    _reachable = {
      for (final endpoint in endpoints) endpoint.choice: BackendService.instance.isReachable(endpoint),
    };
  }

  @override
  Widget build(BuildContext context) {
    final service = BackendService.instance;
    final config = service.config;
    if (config == null) return const SizedBox.shrink();
    final fallback = config.fallback;
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              S.current.server.toUpperCase(),
              style: const TextStyle(
                color: AppColors.textSecondary,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                letterSpacing: 1.5,
              ),
            ),
            const SizedBox(height: 12),
            if (fallback != null)
              _ServerOption(
                icon: Icons.sync_alt_rounded,
                title: S.current.serverAuto,
                subtitle: Text(
                  S.current.serverAutoHint(serverName(config.primary), serverName(fallback)),
                  style: const TextStyle(color: AppColors.textTertiary, fontSize: 12),
                ),
                selected: service.choice == ServerChoice.auto,
                onTap: () => Navigator.of(context).pop(ServerChoice.auto),
              ),
            for (final endpoint in config.endpoints)
              _ServerOption(
                icon: endpoint.kind == BackendKind.cloud ? Icons.cloud_outlined : Icons.developer_board_outlined,
                title: serverLabel(endpoint),
                subtitle: _StatusLine(
                  reachable: _reachable[endpoint.choice],
                  inUse: service.active == endpoint,
                ),
                selected: service.choice == endpoint.choice,
                onTap: () => Navigator.of(context).pop(endpoint.choice),
              ),
            const SizedBox(height: 8),
            Text(
              S.current.serverAccountsNote,
              style: const TextStyle(color: AppColors.textTertiary, fontSize: 11, height: 1.4),
            ),
          ],
        ),
      ),
    );
  }
}

class _ServerOption extends StatelessWidget {
  const _ServerOption({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.selected,
    required this.onTap,
  });

  final IconData icon;
  final String title;
  final Widget subtitle;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return ListTile(
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: selected ? AppColors.cyan : AppColors.textSecondary),
      title: Text(
        title,
        style: const TextStyle(color: AppColors.textPrimary, fontSize: 14, fontWeight: FontWeight.w500),
      ),
      subtitle: subtitle,
      trailing: selected ? const Icon(Icons.check_rounded, color: AppColors.cyan) : null,
      onTap: onTap,
    );
  }
}

class _StatusLine extends StatelessWidget {
  const _StatusLine({required this.reachable, required this.inUse});

  final Future<bool>? reachable;
  final bool inUse;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<bool>(
      future: reachable,
      builder: (context, snapshot) {
        final done = snapshot.connectionState == ConnectionState.done;
        final online = snapshot.data ?? false;
        final status = !done
            ? S.current.serverChecking
            : (online ? S.current.serverOnline : S.current.serverOffline);
        final color = !done ? AppColors.textTertiary : (online ? AppColors.mint : AppColors.red);
        return Text(
          inUse ? '$status · ${S.current.serverInUse}' : status,
          style: TextStyle(color: color, fontSize: 12),
        );
      },
    );
  }
}

/// One button per configured server, for the startup error screen: when the
/// server in use cannot be reached the user can try the other one.
class StartupServerButtons extends StatelessWidget {
  const StartupServerButtons({super.key});

  @override
  Widget build(BuildContext context) {
    final config = BackendService.instance.config;
    if (config == null || !config.hasFallback) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final endpoint in config.endpoints)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: OutlinedButton.icon(
              // A retry is for this run only: it must not turn off Auto for good.
              onPressed: () => BackendService.instance.switchTo(endpoint.choice, remember: false),
              icon: Icon(
                endpoint.kind == BackendKind.cloud ? Icons.cloud_outlined : Icons.developer_board_outlined,
                size: 18,
              ),
              label: Text('${S.current.serverRetry}: ${serverLabel(endpoint)}'),
            ),
          ),
      ],
    );
  }
}

/// Small "server in use" line for the sign-in screen. Accounts belong to one
/// server, so a user who failed over must be able to switch back from here.
class ServerChip extends StatelessWidget {
  const ServerChip({super.key});

  @override
  Widget build(BuildContext context) {
    final service = BackendService.instance;
    final active = service.active;
    if (active == null) return const SizedBox.shrink();
    final canSwitch = service.hasFallback;
    return Center(
      child: TextButton.icon(
        onPressed: canSwitch ? () => showServerPicker(context) : null,
        icon: Icon(
          active.kind == BackendKind.cloud ? Icons.cloud_outlined : Icons.developer_board_outlined,
          size: 16,
          color: AppColors.textTertiary,
        ),
        label: Text(
          '${S.current.server}: ${serverLabel(active)}${canSwitch ? '  ▾' : ''}',
          style: const TextStyle(color: AppColors.textTertiary, fontSize: 12),
        ),
      ),
    );
  }
}
