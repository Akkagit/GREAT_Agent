from great_agent.domain.plans import ExperimentSpec


def default_experiment_spec() -> ExperimentSpec:
    """The same explicit starting configuration offered on the experiment home page."""
    return ExperimentSpec.model_validate({
        'scenario': {'mode': 'static', 'frequency': 'DF', 'ambiguity': 'float', 'reset_hours': 0},
        'date_segments': [{'year': 2024, 'start_doy': 350, 'end_doy': 350}],
        'station_group_id': 'default_15',
        'stations': ['AHB0', 'BJB0', 'GDA4', 'GSE4', 'GXC4', 'HBB2', 'HJE2', 'HNB1',
                     'JL18', 'LNC1', 'QHA4', 'SCI5', 'SD10', 'SXE4', 'YNJ8'],
    })
