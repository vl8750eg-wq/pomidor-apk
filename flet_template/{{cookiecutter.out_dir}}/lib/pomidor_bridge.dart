import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:path/path.dart' as path;
import 'package:path_provider/path_provider.dart';

/// Pomidor native alarm bridge.
///
/// The embedded Python program cannot call Android APIs directly, so it writes
/// a small command JSON file (`pomidor_cmd.json`) into the app data directory.
/// This bridge polls the file every 400 ms and forwards each new command
/// (matched by its unique `id`) to the Kotlin side via the `pomidor/native`
/// method channel. Kotlin schedules exact system alarms, posts full-screen
/// notifications and plays the alarm sound (see MainActivity.kt).
///
/// The ack JSON (`pomidor_ack.json`) carries the notification/alarm capability
/// status back to Python so the UI can show hints (e.g. missing permission).
///
/// Safety properties:
///   - every command carries a unique id; a command is executed exactly once,
///   - stale commands replayed after a cold start are harmless: past `ts_ms`
///     alarms are skipped by Kotlin and `play` carries an `until` timestamp,
///   - every failure is swallowed — the timer app itself must never crash
///     because of the alarm bridge.

const String _channelName = 'pomidor/native';
const String _cmdFileName = 'pomidor_cmd.json';
const String _ackFileName = 'pomidor_ack.json';

MethodChannel? _channel;
Timer? _timer;

Future<void> startPomidorBridge() async {
  if (kIsWeb) return;
  try {
    if (defaultTargetPlatform != TargetPlatform.android) return;
    final support = await getApplicationSupportDirectory();
    final dataDir = Directory(path.join(support.path, 'data'));
    if (!await dataDir.exists()) {
      await dataDir.create(recursive: true);
    }
    final cmdFile = File(path.join(dataDir.path, _cmdFileName));
    final ackFile = File(path.join(dataDir.path, _ackFileName));
    _channel = const MethodChannel(_channelName);
    String lastId = '';
    _timer = Timer.periodic(const Duration(milliseconds: 400), (_) async {
      String raw = '';
      try {
        if (await cmdFile.exists()) {
          raw = await cmdFile.readAsString();
        }
      } catch (_) {
        return;
      }
      if (raw.isEmpty) return;
      Object? decoded;
      try {
        decoded = jsonDecode(raw);
      } catch (_) {
        return;
      }
      if (decoded is! Map) return;
      final String id = (decoded['id'] ?? '').toString();
      if (id.isEmpty || id == lastId) return;
      Map<Object?, Object?>? res;
      String err = '';
      try {
        res = await _channel!.invokeMethod('exec', decoded);
      } on PlatformException catch (e) {
        err = e.message ?? e.code;
      } catch (e) {
        err = e.toString();
      }
      lastId = id;
      try {
        final ack = jsonEncode({
          'id': id,
          'ok': err.isEmpty,
          'error': err,
          'notif': res?['notif'] ?? false,
          'exact': res?['exact'] ?? false,
        });
        await ackFile.writeAsString(ack, flush: true);
      } catch (_) {
        // ack is best-effort only
      }
    });
  } catch (_) {
    // the alarm bridge is optional — degrade silently on any failure
  }
}
