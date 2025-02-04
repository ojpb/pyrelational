"""
This is a toy self-contained on how to use Badge with the pyrelational package.

About BADGE algorithm: https://arxiv.org/abs/1906.03671
"""

import logging
from typing import List

import torch

# Dataset and machine learning model
from examples.utils.datasets import BreastCancerDataset  # noqa: E402
from examples.utils.ml_models import BreastCancerClassification  # noqa: E402

# Active Learning package
from pyrelational.data import GenericDataManager
from pyrelational.informativeness import relative_distance
from pyrelational.models import LightningModel
from pyrelational.strategies.generic_al_strategy import GenericActiveLearningStrategy

# dataset
dataset = BreastCancerDataset()
train_ds, val_ds, test_ds = torch.utils.data.random_split(dataset, [500, 30, 39])
train_indices = train_ds.indices
val_indices = val_ds.indices
test_indices = test_ds.indices


# model


class BadgeLightningModel(LightningModel):
    """Model compatible with BADGE strategy"""

    def __init__(self, model_class, model_config, trainer_config):
        super(BadgeLightningModel, self).__init__(model_class, model_config, trainer_config)

    def get_gradients(self, loader):
        """
        Get gradients for each sample in dataloader as outlined in BADGE paper.

        Assumes the last layer is a linear layer and return_penultimate_embed/criterion is defined in the model class
        :param loader: dataloader
        :return: tensor of gradients for each sample
        """
        if self.current_model is None:
            raise ValueError(
                """
                    Trying to query gradients of an untrained model,
                    train model before calling get_gradients.
                """
            )

        model = self.current_model
        model.eval()
        gradients = []
        for x, _ in loader:
            model.zero_grad()
            logits = model(x)
            class_preds = torch.argmax(logits, dim=1)
            loss = model.criterion(logits, class_preds)  # assumes criterion is defined in model class
            e = model.return_penultimate_embed(x)
            # find gradients of bias in last layer
            bias_grad = torch.autograd.grad(loss, logits)[0]
            # find gradients of weights in last layer
            weights_grad = torch.einsum("be,bc -> bec", e, bias_grad)
            gradients.append(torch.cat([weights_grad.detach().cpu(), bias_grad.unsqueeze(1).detach().cpu()], 1))

        return torch.cat(gradients, 0)


model = BadgeLightningModel(model_class=BreastCancerClassification, model_config={}, trainer_config={"epochs": 5})

# data_manager and defining strategy
data_manager = GenericDataManager(
    dataset=dataset,
    train_indices=train_indices,
    validation_indices=val_indices,
    test_indices=test_indices,
    loader_batch_size=16,
)


class BadgeStrategy(GenericActiveLearningStrategy):
    """Implementation of BADGE strategy."""

    def __init__(self, data_manager: GenericDataManager, model: BadgeLightningModel):
        super(BadgeStrategy, self).__init__(data_manager, model)

    def active_learning_step(self, num_annotate: int) -> List[int]:
        """
        :param num_annotate: Number of samples to label
        :return: indices of samples to label
        """
        self.model.train(self.l_loader, self.valid_loader)
        u_grads = self.model.get_gradients(self.u_loader)
        l_grads = self.model.get_gradients(self.l_loader)
        scores = relative_distance(u_grads, l_grads)
        ixs = torch.argsort(scores, descending=True).tolist()
        return [self.u_indices[i] for i in ixs[:num_annotate]]


strategy = BadgeStrategy(data_manager=data_manager, model=model)

# Remove lightning prints
logging.getLogger("pytorch_lightning").setLevel(logging.ERROR)

# performance with the full trainset labelled
strategy.theoretical_performance()

# New data to be annotated, followed by an update of the data_manager and model
to_annotate = strategy.active_learning_step(num_annotate=100)
strategy.active_learning_update(indices=to_annotate, update_tag="Manual Update")

# Annotating data step by step until the trainset is fully annotated
strategy.full_active_learning_run(num_annotate=100)
print(strategy)
