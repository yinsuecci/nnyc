import 'package:flutter_webrtc/flutter_webrtc.dart';

enum IcePath {
  unknown('路径待确认'),
  direct('设备直连'),
  mapped('映射地址直连'),
  relay('中继连接');

  final String label;
  const IcePath(this.label);
}

/// Only report the selected transport of the received video, never a gathered
/// candidate or the presence of a configured STUN server.
IcePath videoIcePath(List<StatsReport> reports) {
  final byId = {for (final report in reports) report.id: report};
  final videos = reports.where((r) =>
      r.type == 'inbound-rtp' &&
      (r.values['kind'] ?? r.values['mediaType']) == 'video');
  final transports = videos.map((r) => r.values['transportId']).toSet();
  if (transports.length != 1) return IcePath.unknown;
  final transport = byId[transports.single];
  if (transport?.type != 'transport' ||
      transport!.values['dtlsState'] != 'connected') {
    return IcePath.unknown;
  }
  final pair = byId[transport.values['selectedCandidatePairId']];
  if (pair?.type != 'candidate-pair' || pair!.values['state'] != 'succeeded') {
    return IcePath.unknown;
  }
  final local = byId[pair.values['localCandidateId']];
  final remote = byId[pair.values['remoteCandidateId']];
  if (local?.type != 'local-candidate' || remote?.type != 'remote-candidate') {
    return IcePath.unknown;
  }
  final kinds = {
    local!.values['candidateType'],
    remote!.values['candidateType']
  };
  if (kinds.contains('relay')) return IcePath.relay;
  if (!kinds.every({'host', 'srflx', 'prflx'}.contains)) return IcePath.unknown;
  return kinds.contains('srflx') || kinds.contains('prflx')
      ? IcePath.mapped
      : IcePath.direct;
}
