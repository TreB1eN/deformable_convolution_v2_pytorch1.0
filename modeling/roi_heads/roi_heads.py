# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved.
import torch
from torch import nn
from modeling import registry
from .box_head.box_head import build_roi_box_head
from .mask_head.mask_head import build_roi_mask_head
from layers import Conv2d
import torch.nn.functional as F
from layers import Conv2d
from layers import ConvTranspose2d

@registry.COMBINED_ROI_HEADS.register("CombinedROIHeads")
class CombinedROIHeads(torch.nn.ModuleDict):
    """
    Combines a set of individual heads (for box prediction or masks) into a single
    head.
    """
    def __init__(self, cfg, heads):
        super(CombinedROIHeads, self).__init__(heads)
        self.cfg = cfg.clone()
        if cfg.MODEL.MASK_ON and cfg.MODEL.ROI_MASK_HEAD.SHARE_BOX_FEATURE_EXTRACTOR:
            self.mask.feature_extractor = self.box.feature_extractor

    def forward(self, features, proposals, targets=None):
        losses = {}
        # TODO rename x to roi_box_features, if it doesn't increase memory consumption
        x, detections, loss_box = self.box(features, proposals, targets)
        losses.update(loss_box)
        if self.cfg.MODEL.MASK_ON:
            mask_features = features
            # optimization: during training, if we share the feature extractor between
            # the box and the mask heads, then we can reuse the features already computed
            if (
                self.training
                and self.cfg.MODEL.ROI_MASK_HEAD.SHARE_BOX_FEATURE_EXTRACTOR
            ):
                mask_features = x
            # During training, self.box() will return the unaltered proposals as "detections"
            # this makes the API consistent during training and testing
            x, detections, loss_mask = self.mask(mask_features, detections, targets)
            losses.update(loss_mask)
        return x, detections, losses

@registry.COMBINED_ROI_HEADS.register("DeformableCombinedROIHeads")
class DeformableCombinedROIHeads(torch.nn.ModuleDict):
    """
    Combines a set of individual heads (for box prediction or masks) into a single
    head.
    """
    def __init__(self, cfg, heads):
        super(DeformableCombinedROIHeads, self).__init__(heads)
        self.cfg = cfg.clone()
        if cfg.MODEL.MASK_ON and cfg.MODEL.ROI_MASK_HEAD.SHARE_BOX_FEATURE_EXTRACTOR:
            self.mask.feature_extractor = self.box.feature_extractor
        feature_channels = cfg.MODEL.BACKBONE.C5_CHANNELS
        channels_before_Pooling = cfg.MODEL.ROI_BOX_HEAD.CHANNELS_BEFORE_POOLING
        self.conv = Conv2d(feature_channels, channels_before_Pooling, 1)
    
    def reset_parameters(self):
        for module in self.parameters():
            if isinstance(module, Conv2d) or isinstance(module, ConvTranspose2d):
                # Caffe2 implementation uses XavierFill, which in fact
                # corresponds to kaiming_uniform_ in PyTorch
                nn.init.kaiming_uniform_(module.weight, a=1)
                if module.bias is not None:
                    nn.init.constant_(module.bias, 0)

    def forward(self, features, proposals, targets=None):
        losses = {}
        # TODO rename x to roi_box_features, if it doesn't increase memory consumption
        features = F.relu_(self.conv(features))
        box_pooled_feature, box_mimicking_feature, detections, loss_box = self.box(features, proposals, targets)
        losses.update(loss_box)
        if self.cfg.MODEL.MASK_ON:
            mask_features = features
            # optimization: during training, if we share the feature extractor between
            # the box and the mask heads, then we can reuse the features already computed
            if (
                self.training
                and self.cfg.MODEL.ROI_MASK_HEAD.SHARE_BOX_FEATURE_EXTRACTOR
            ):
                mask_features = box_pooled_feature
            # During training, self.box() will return the unaltered proposals as "detections"
            # this makes the API consistent during training and testing
            mask_features, box_mimicking_feature, detections, _, loss_mask = self.mask(mask_features, box_mimicking_feature, detections, targets)
            losses.update(loss_mask)
        return mask_features, box_mimicking_feature, detections, losses

def build_roi_heads(cfg):
    # individually create the heads, that will be combined together
    # afterwards
    roi_heads = []
    if not cfg.MODEL.RPN_ONLY:
        roi_heads.append(("box", build_roi_box_head(cfg)))
    if cfg.MODEL.MASK_ON:
        roi_heads.append(("mask", build_roi_mask_head(cfg)))

    # combine individual heads in a single module
    if roi_heads:
        roi_heads = make_combined_ROIHeads(cfg, roi_heads)

    return roi_heads

def make_combined_ROIHeads(cfg, heads):
    func = registry.COMBINED_ROI_HEADS[cfg.MODEL.ROI_HEADS.COMBINED_ROI_HEADS]
    return func(cfg, heads)
