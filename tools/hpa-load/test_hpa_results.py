"""New review regression only: all nonzero HPA phases must meet the result gate."""
import unittest
import common as C
import results


def fixture():
    config={'run_id':'hpa-result-regression','binding':{'cluster_id':'synthetic-new-cluster'},'min_replicas':2}
    bound={'binding':config['binding'],'config_sha256':C.digest(config)}
    before={'config_sha256':C.digest(config),'deployments':{'backend':{'spec':{'replicas':2}}}}
    load={'binding':config['binding'],'control_sha256':C.digest(config),
          'status':'COMPLETED_HPA_LOAD','guest_cleanup_unconfirmed':0}
    counts={'binding':config['binding'],'run_id':config['run_id'],'control_sha256':C.digest(config),
            'phases':[{'phase':i,'vus':vus,'N':1000 if vus else 0,
                       'http_5xx_fraction':0 if vus else None}
                      for i,(vus,_) in enumerate(C.PHASES)]}
    rows=[{**bound,'status':'PASS_HPA_GUARD','hpa':{'status':{'currentReplicas':n}},
           'db':{'connections_total':5,'global_limit':68}} for n in (2,4,2)]
    restored={**bound,'status':'PASS_HPA_REMOVED_ORIGINAL_SPEC_REPLICAS_READY_NOT_BUSINESS_ACCEPTANCE'}
    return [config,before,load,counts,rows,restored]


class ResultThresholdTests(unittest.TestCase):
    def partial(self,inputs,phase,reason):
        value=results.summarize(*inputs)
        self.assertEqual(value['status'],'PARTIAL_OR_THRESHOLD_NOT_MET_HPA_RESULT')
        self.assertFalse(value['all_nonzero_phases_http_5xx_below_1_percent'])
        row=next(x for x in value['phase_acceptance'] if x['phase']==phase)
        self.assertEqual(row['status'],reason)
        return value

    def test_all_six_nonzero_phases_must_pass(self):
        value=results.summarize(*fixture())
        self.assertEqual(value['status'],'OBSERVED_HPA_SCALE_OUT_IN_SCOPED_HTTP_RESULT')
        self.assertTrue(value['all_nonzero_phases_http_5xx_below_1_percent'])
        self.assertEqual([x['phase'] for x in value['phase_acceptance']],[1,2,3,4,5,6])
        self.assertEqual([x['vus'] for x in value['phase_acceptance']],[10,50,100,200,300,100])
        self.assertNotIn('phase_1_to_4_http_5xx_below_1_percent',value)

    def test_maximum_300_vu_failure_cannot_be_success(self):
        inputs=fixture();inputs[3]['phases'][5]['http_5xx_fraction']=.02
        self.partial(inputs,5,'HTTP_5XX_THRESHOLD_NOT_MET')

    def test_return_100_vu_failure_cannot_be_success(self):
        inputs=fixture();inputs[3]['phases'][6]['http_5xx_fraction']=.02
        self.partial(inputs,6,'HTTP_5XX_THRESHOLD_NOT_MET')

    def test_maximum_300_vu_zero_samples_cannot_be_success(self):
        inputs=fixture();inputs[3]['phases'][5].update(N=0,http_5xx_fraction=None)
        self.partial(inputs,5,'NO_VALID_SAMPLES')

    def test_return_100_vu_zero_samples_cannot_be_success(self):
        inputs=fixture();inputs[3]['phases'][6].update(N=0,http_5xx_fraction=None)
        self.partial(inputs,6,'NO_VALID_SAMPLES')

    def test_missing_maximum_or_return_phase_cannot_be_success(self):
        for index in (5,6):
            with self.subTest(phase=index):
                inputs=fixture();inputs[3]['phases']=[x for x in inputs[3]['phases'] if x['phase']!=index]
                self.partial(inputs,index,'MISSING_PHASE')

    def test_maximum_or_return_no_data_error_rate_cannot_be_success(self):
        for index in (5,6):
            with self.subTest(phase=index):
                inputs=fixture();inputs[3]['phases'][index]['http_5xx_fraction']=None
                self.partial(inputs,index,'NO_VALID_ERROR_RATE')

    def test_one_percent_is_not_below_threshold_for_any_nonzero_phase(self):
        for index in range(1,7):
            with self.subTest(phase=index):
                inputs=fixture();inputs[3]['phases'][index]['http_5xx_fraction']=.01
                self.partial(inputs,index,'HTTP_5XX_THRESHOLD_NOT_MET')

    def test_only_zero_vu_phases_may_have_no_samples(self):
        inputs=fixture();inputs[3]['phases']=[x for x in inputs[3]['phases'] if x['vus']]
        self.assertEqual(results.summarize(*inputs)['status'],'OBSERVED_HPA_SCALE_OUT_IN_SCOPED_HTTP_RESULT')


if __name__=='__main__':unittest.main()
