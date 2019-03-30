# -*- coding: utf-8 -*-
"""
Fast R-CNN
Copyright (c) 2015 Microsoft
Licensed under The MIT License [see LICENSE for details]
Written by Ross Girshick
"""
from __future__ import absolute_import, division, print_function, unicode_literals
import numpy as np


def py_nms(np_tlbr, np_scores, thresh, bias=1):
    """
    Pure Python NMS baseline.

    Example:
        >>> np_tlbr = np.array([
        >>>     [0, 0, 100, 100],
        >>>     [100, 100, 10, 10],
        >>>     [10, 10, 100, 100],
        >>>     [50, 50, 100, 100],
        >>>     [100, 100, 150, 101],
        >>>     [120, 100, 180, 101],
        >>>     [150, 100, 200, 101],
        >>> ], dtype=np.float32)
        >>> np_scores = np.linspace(0, 1, len(np_tlbr))
        >>> thresh = 0.1
        >>> bias = 0.0
        >>> keep = sorted(py_nms(np_tlbr, np_scores, thresh, bias))
        >>> print('keep = {!r}'.format(keep))
        keep = [1, 3, 4, 6]
    """
    x1 = np_tlbr[:, 0]
    y1 = np_tlbr[:, 1]
    x2 = np_tlbr[:, 2]
    y2 = np_tlbr[:, 3]

    areas = (x2 - x1 + bias) * (y2 - y1 + bias)

    order = np_scores.argsort()[::-1]

    keep = []
    # n_conflicts = 0
    while order.size > 0:
        i = order[0]
        keep.append(i)

        js_remain = order[1:]
        xx1 = np.maximum(x1[i], x1[js_remain])
        yy1 = np.maximum(y1[i], y1[js_remain])
        xx2 = np.minimum(x2[i], x2[js_remain])
        yy2 = np.minimum(y2[i], y2[js_remain])

        w = np.maximum(0.0, xx2 - xx1 + bias)
        h = np.maximum(0.0, yy2 - yy1 + bias)
        inter = w * h
        ovr = inter / (areas[i] + areas[js_remain] - inter)
        ovr = np.nan_to_num(ovr)

        # Remove any indices that (significantly) overlap with this item
        # NOTE: We are using following convention:
        #     * suppress if overlap > thresh
        #     * consider if overlap <= thresh
        # This convention has the property that when thresh=0, we dont just
        # remove everything.
        flags = ovr <= thresh
        print('flags = {!r}'.format(flags))
        inds = np.where(flags)[0]
        order = order[inds + 1]

    return keep
