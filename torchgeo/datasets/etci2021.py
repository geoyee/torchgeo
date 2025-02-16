# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

"""ETCI 2021 dataset."""

import glob
import os
from typing import Any, Callable, Dict, List, Optional

import matplotlib.pyplot as plt
import numpy as np
import pytorch_lightning as pl
import torch
from PIL import Image
from torch import Generator, Tensor  # type: ignore[attr-defined]
from torch.utils.data import DataLoader, random_split
from torchvision.transforms import Normalize

from .geo import VisionDataset
from .utils import download_and_extract_archive


class ETCI2021(VisionDataset):
    """ETCI 2021 Flood Detection dataset.

    The `ETCI2021 <https://nasa-impact.github.io/etci2021/>`_
    dataset is a dataset for flood detection

    Dataset features:

    * 33,405 VV & VH Sentinel-1 Synthetic Aperture Radar (SAR) images
    * 2 binary masks per image representing water body & flood, respectively
    * 2 polarization band images (VV, VH) of 3 RGB channels per band
    * 3 RGB channels per band generated by the Hybrid Pluggable Processing
      Pipeline (hyp3)
    * Images with 5x20m per pixel resolution (256x256) px) taken in
      Interferometric Wide Swath acquisition mode
    * Flood events from 5 different regions

    Dataset format:

    * VV band three-channel png
    * VH band three-channel png
    * water body mask single-channel png where no water body = 0, water body = 255
    * flood mask single-channel png where no flood = 0, flood = 255

    Dataset classes:

    1. no flood/water
    2. flood/water

    If you use this dataset in your research, please add the following to your
    acknowledgements section::

        The authors would like to thank the NASA Earth Science Data Systems Program,
        NASA Digital Transformation AI/ML thrust, and IEEE GRSS for organizing
        the ETCI competition.
    """

    bands = ["VV", "VH"]
    masks = ["flood", "water_body"]
    metadata = {
        "train": {
            "filename": "train.zip",
            "md5": "1e95792fe0f6e3c9000abdeab2a8ab0f",
            "directory": "train",
            "url": "https://drive.google.com/file/d/14HqNW5uWLS92n7KrxKgDwUTsSEST6LCr",
        },
        "val": {
            "filename": "val_with_ref_labels.zip",
            "md5": "fd18cecb318efc69f8319f90c3771bdf",
            "directory": "test",
            "url": "https://drive.google.com/file/d/19sriKPHCZLfJn_Jmk3Z_0b3VaCBVRVyn",
        },
        "test": {
            "filename": "test_without_ref_labels.zip",
            "md5": "da9fa69e1498bd49d5c766338c6dac3d",
            "directory": "test_internal",
            "url": "https://drive.google.com/file/d/1rpMVluASnSHBfm2FhpPDio0GyCPOqg7E",
        },
    }

    def __init__(
        self,
        root: str = "data",
        split: str = "train",
        transforms: Optional[Callable[[Dict[str, Tensor]], Dict[str, Tensor]]] = None,
        download: bool = False,
        checksum: bool = False,
    ) -> None:
        """Initialize a new ETCI 2021 dataset instance.

        Args:
            root: root directory where dataset can be found
            split: one of "train", "val", or "test"
            transforms: a function/transform that takes input sample and its target as
                entry and returns a transformed version
            download: if True, download dataset and store it in the root directory
            checksum: if True, check the MD5 of the downloaded files (may be slow)

        Raises:
            AssertionError: if ``split`` argument is invalid
            RuntimeError: if ``download=False`` and data is not found, or checksums
                don't match
        """
        assert split in self.metadata.keys()

        self.root = root
        self.split = split
        self.transforms = transforms
        self.checksum = checksum

        if download:
            self._download()

        if not self._check_integrity():
            raise RuntimeError(
                "Dataset not found or corrupted. "
                + "You can use download=True to download it"
            )

        self.files = self._load_files(self.root, self.split)

    def __getitem__(self, index: int) -> Dict[str, Tensor]:
        """Return an index within the dataset.

        Args:
            index: index to return

        Returns:
            data and label at that index
        """
        files = self.files[index]
        vv = self._load_image(files["vv"])
        vh = self._load_image(files["vh"])
        water_mask = self._load_target(files["water_mask"])

        if self.split != "test":
            flood_mask = self._load_target(files["flood_mask"])
            mask = torch.stack(tensors=[water_mask, flood_mask], dim=0)
        else:
            mask = water_mask.unsqueeze(0)

        image = torch.cat(tensors=[vv, vh], dim=0)  # type: ignore[attr-defined]
        sample = {"image": image, "mask": mask}

        if self.transforms is not None:
            sample = self.transforms(sample)

        return sample

    def __len__(self) -> int:
        """Return the number of data points in the dataset.

        Returns:
            length of the dataset
        """
        return len(self.files)

    def _load_files(self, root: str, split: str) -> List[Dict[str, str]]:
        """Return the paths of the files in the dataset.

        Args:
            root: root dir of dataset
            split: subset of dataset, one of [train, val, test]

        Returns:
            list of dicts containing paths for each pair of vv, vh,
            water body mask, flood mask (train/val only)
        """
        files = []
        directory = self.metadata[split]["directory"]
        folders = sorted(glob.glob(os.path.join(root, directory, "*")))
        folders = [os.path.join(folder, "tiles") for folder in folders]
        for folder in folders:
            vvs = sorted(glob.glob(os.path.join(folder, "vv", "*.png")))
            vhs = sorted(glob.glob(os.path.join(folder, "vh", "*.png")))
            water_masks = sorted(
                glob.glob(os.path.join(folder, "water_body_label", "*.png"))
            )

            if split != "test":
                flood_masks = sorted(
                    glob.glob(os.path.join(folder, "flood_label", "*.png"))
                )

                for vv, vh, flood_mask, water_mask in zip(
                    vvs, vhs, flood_masks, water_masks
                ):
                    files.append(
                        dict(vv=vv, vh=vh, flood_mask=flood_mask, water_mask=water_mask)
                    )
            else:
                for vv, vh, water_mask in zip(vvs, vhs, water_masks):
                    files.append(dict(vv=vv, vh=vh, water_mask=water_mask))

        return files

    def _load_image(self, path: str) -> Tensor:
        """Load a single image.

        Args:
            path: path to the image

        Returns:
            the image
        """
        filename = os.path.join(path)
        with Image.open(filename) as img:
            array = np.array(img.convert("RGB"))
            tensor: Tensor = torch.from_numpy(array)  # type: ignore[attr-defined]
            # Convert from HxWxC to CxHxW
            tensor = tensor.permute((2, 0, 1))
            return tensor

    def _load_target(self, path: str) -> Tensor:
        """Load the target mask for a single image.

        Args:
            path: path to the image

        Returns:
            the target mask
        """
        filename = os.path.join(path)
        with Image.open(filename) as img:
            array = np.array(img.convert("L"))
            tensor: Tensor = torch.from_numpy(array)  # type: ignore[attr-defined]
            tensor = torch.clamp(tensor, min=0, max=1)  # type: ignore[attr-defined]
            tensor = tensor.to(torch.long)  # type: ignore[attr-defined]
            return tensor

    def _check_integrity(self) -> bool:
        """Checks the integrity of the dataset structure.

        Returns:
            True if the dataset directories and split files are found, else False
        """
        directory = self.metadata[self.split]["directory"]
        dirpath = os.path.join(self.root, directory)
        if not os.path.exists(dirpath):
            return False
        return True

    def _download(self) -> None:
        """Download the dataset and extract it.

        Raises:
            AssertionError: if the checksum of split.py does not match
        """
        if self._check_integrity():
            print("Files already downloaded and verified")
            return

        download_and_extract_archive(
            self.metadata[self.split]["url"],
            self.root,
            filename=self.metadata[self.split]["filename"],
            md5=self.metadata[self.split]["md5"] if self.checksum else None,
        )

    def plot(
        self,
        sample: Dict[str, Tensor],
        show_titles: bool = True,
        suptitle: Optional[str] = None,
    ) -> plt.Figure:
        """Plot a sample from the dataset.

        Args:
            sample: a sample returned by :meth:`__getitem__`
            show_titles: flag indicating whether to show titles above each panel
            suptitle: optional string to use as a suptitle

        Returns:
            a matplotlib Figure with the rendered sample
        """
        vv = np.rollaxis(sample["image"][:3].numpy(), 0, 3)
        vh = np.rollaxis(sample["image"][3:].numpy(), 0, 3)
        water_mask = sample["mask"][0].numpy()

        showing_flood_mask = sample["mask"].shape[0] > 1
        showing_predictions = "prediction" in sample
        num_panels = 3
        if showing_flood_mask:
            flood_mask = sample["mask"][1].numpy()
            num_panels += 1

        if showing_predictions:
            predictions = sample["prediction"].numpy()
            num_panels += 1

        fig, axs = plt.subplots(1, num_panels, figsize=(num_panels * 4, 3))
        axs[0].imshow(vv)
        axs[0].axis("off")
        axs[1].imshow(vh)
        axs[1].axis("off")
        axs[2].imshow(water_mask)
        axs[2].axis("off")
        if show_titles:
            axs[0].set_title("VV")
            axs[1].set_title("VH")
            axs[2].set_title("Water mask")

        idx = 0
        if showing_flood_mask:
            axs[3 + idx].imshow(flood_mask)
            axs[3 + idx].axis("off")
            if show_titles:
                axs[3 + idx].set_title("Flood mask")
            idx += 1

        if showing_predictions:
            axs[3 + idx].imshow(predictions)
            axs[3 + idx].axis("off")
            if show_titles:
                axs[3 + idx].set_title("Predictions")
            idx += 1

        if suptitle is not None:
            plt.suptitle(suptitle)
        return fig


class ETCI2021DataModule(pl.LightningDataModule):
    """LightningDataModule implementation for the ETCI2021 dataset.

    Splits the existing train split from the dataset into train/val with 80/20
    proportions, then uses the existing val dataset as the test data.

    .. versionadded:: 0.2
    """

    band_means = torch.tensor(  # type: ignore[attr-defined]
        [0.52253931, 0.52253931, 0.52253931, 0.61221701, 0.61221701, 0.61221701, 0]
    )

    band_stds = torch.tensor(  # type: ignore[attr-defined]
        [0.35221376, 0.35221376, 0.35221376, 0.37364622, 0.37364622, 0.37364622, 1]
    )

    def __init__(
        self,
        root_dir: str,
        seed: int = 0,
        batch_size: int = 64,
        num_workers: int = 0,
        **kwargs: Any,
    ) -> None:
        """Initialize a LightningDataModule for ETCI2021 based DataLoaders.

        Args:
            root_dir: The ``root`` arugment to pass to the ETCI2021 Dataset classes
            seed: The seed value to use when doing the dataset random_split
            batch_size: The batch size to use in all created DataLoaders
            num_workers: The number of workers to use in all created DataLoaders
        """
        super().__init__()  # type: ignore[no-untyped-call]
        self.root_dir = root_dir
        self.seed = seed
        self.batch_size = batch_size
        self.num_workers = num_workers

        self.norm = Normalize(self.band_means, self.band_stds)

    def preprocess(self, sample: Dict[str, Any]) -> Dict[str, Any]:
        """Transform a single sample from the Dataset.

        Notably, moves the given water mask to act as an input layer.

        Args:
            sample: input image dictionary

        Returns:
            preprocessed sample
        """
        image = sample["image"]
        water_mask = sample["mask"][0].unsqueeze(0)
        flood_mask = sample["mask"][1]
        flood_mask = (flood_mask > 0).long()

        sample["image"] = torch.cat(  # type: ignore[attr-defined]
            [image, water_mask], dim=0
        ).float()
        sample["image"] /= 255.0
        sample["image"] = self.norm(sample["image"])
        sample["mask"] = flood_mask
        return sample

    def prepare_data(self) -> None:
        """Make sure that the dataset is downloaded.

        This method is only called once per run.
        """
        ETCI2021(self.root_dir, checksum=False)

    def setup(self, stage: Optional[str] = None) -> None:
        """Initialize the main ``Dataset`` objects.

        This method is called once per GPU per run.

        Args:
            stage: stage to set up
        """
        train_val_dataset = ETCI2021(
            self.root_dir, split="train", transforms=self.preprocess
        )
        self.test_dataset = ETCI2021(
            self.root_dir, split="val", transforms=self.preprocess
        )

        size_train_val = len(train_val_dataset)
        size_train = int(0.8 * size_train_val)
        size_val = size_train_val - size_train

        self.train_dataset, self.val_dataset = random_split(
            train_val_dataset,
            [size_train, size_val],
            generator=Generator().manual_seed(self.seed),
        )

    def train_dataloader(self) -> DataLoader[Any]:
        """Return a DataLoader for training.

        Returns:
            training data loader
        """
        return DataLoader(
            self.train_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
            shuffle=True,
        )

    def val_dataloader(self) -> DataLoader[Any]:
        """Return a DataLoader for validation.

        Returns:
            validation data loader
        """
        return DataLoader(
            self.val_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
            shuffle=False,
        )

    def test_dataloader(self) -> DataLoader[Any]:
        """Return a DataLoader for testing.

        Returns:
            testing data loader
        """
        return DataLoader(
            self.test_dataset,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
            shuffle=False,
        )
