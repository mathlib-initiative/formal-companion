"""The PDF-to-companion pipeline's own records: ``pipeline.yaml`` and stage receipts.

New code for milestone M0a of docs/proposals/pdf-to-companion.md (section
4.1).  A pipeline run is one directory, ``pipelines/<id>/``, holding the
pipeline-wide settings (``pipeline.yaml``: exercise policy, scope, consent,
the run id of every fleet a stage planned), the checkpointed annotation
documents and one receipt per stage.  ``pipeline_yaml.py`` loads and
validates the settings file and refuses a key it does not know;
``stage_receipt.py`` writes ``stages/<stage>.stage-receipt.json`` and fails
closed on a missing field or a sha of the wrong length.  No stage of the
pipeline is built yet: these are the records the stages will write.
"""
