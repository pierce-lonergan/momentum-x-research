"""doc 273: plants on the CLEAN universe (spine_clean), artifacts -> doc273_plants_clean.parquet.
Same plant spec as registered (drivers/strengths/reps unchanged)."""
import importlib.util

spec = importlib.util.spec_from_file_location("kf", "scripts/knowability_frontier_doc273.py")
kf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kf)

kf.SPINE = kf.SPINE.replace(".parquet", "_clean.parquet")
kf.PLANTF = kf.PLANTF.replace(".parquet", "_clean.parquet")
kf.run_plant()
