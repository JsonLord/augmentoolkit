from collections.abc import Callable
from typing import Union

from transformers import (
    PreTrainedModel,
)
from transformers.utils.import_utils import _is_package_available
from trl import GRPOConfig, GRPOTrainer

_vllm_available = _is_package_available("vllm")

if _vllm_available:
    from vllm import SamplingParams


RewardFunc = Union[str, PreTrainedModel, Callable[[list, list], list[float]]]


class GRPOTrainerStopSequences(GRPOTrainer):
    def __init__(
        self,
        model: str | PreTrainedModel,
        reward_funcs: RewardFunc | list[RewardFunc],
        args: GRPOConfig = None,
        *otherargs,
        stop=None,
        **kwargs,
    ):
        if stop is None:
            stop = ["**Finished.**", "Human:"]
        super().__init__(model, reward_funcs, args, *otherargs, **kwargs)
        self.stop = stop
        # overwrite self sampling params
        self.sampling_params = SamplingParams(
            n=self.num_generations,
            temperature=args.temperature,
            max_tokens=self.max_completion_length,
            stop=stop,
        )
