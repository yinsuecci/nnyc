import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_webrtc/flutter_webrtc.dart';
import 'package:nn_remote/ice_path.dart';

void main() {
  List<StatsReport> reports(String local, String remote) => [
        StatsReport(
            'v', 'inbound-rtp', 0, {'kind': 'video', 'transportId': 't'}),
        StatsReport('t', 'transport', 0,
            {'dtlsState': 'connected', 'selectedCandidatePairId': 'p'}),
        StatsReport('p', 'candidate-pair', 0, {
          'state': 'succeeded',
          'localCandidateId': 'l',
          'remoteCandidateId': 'r'
        }),
        StatsReport('l', 'local-candidate', 0, {'candidateType': local}),
        StatsReport('r', 'remote-candidate', 0, {'candidateType': remote}),
      ];
  test('classifies only the pair actually selected for video', () {
    expect(videoIcePath(reports('host', 'host')), IcePath.direct);
    expect(videoIcePath(reports('srflx', 'prflx')), IcePath.mapped);
    expect(videoIcePath(reports('srflx', 'relay')), IcePath.relay);
    final data = reports('host', 'host')
      ..add(StatsReport(
          'unused', 'local-candidate', 0, {'candidateType': 'relay'}));
    expect(videoIcePath(data), IcePath.direct);
    data[2].values['state'] = 'failed';
    expect(videoIcePath(data), IcePath.unknown);
  });
  test('gathered or nominated candidates alone never prove a path', () {
    final data = reports('srflx', 'host');
    data[1].values.remove('selectedCandidatePairId');
    data[2].values['nominated'] = true;
    expect(videoIcePath(data), IcePath.unknown);
    expect(videoIcePath(data.sublist(3)), IcePath.unknown);
  });
}
