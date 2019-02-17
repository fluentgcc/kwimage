# -*- coding: utf-8 -*-
"""
Not sure how to best classify these functions
"""
from __future__ import absolute_import, division, print_function, unicode_literals
import numpy as np


def num_channels(img):
    """ Returns the number of color channels """
    ndims = len(img.shape)
    if ndims == 2:
        nChannels = 1
    elif ndims == 3 and img.shape[2] == 3:
        nChannels = 3
    elif ndims == 3 and img.shape[2] == 4:
        nChannels = 4
    elif ndims == 3 and img.shape[2] == 1:
        nChannels = 1
    else:
        raise ValueError('Cannot determine number of channels '
                         'for img.shape={}'.format(img.shape))
    return nChannels


def ensure_float01(img, dtype=np.float32, copy=True):
    """
    Ensure that an image is encoded using a float32 properly

    Example:
        >>> ensure_float01(np.array([[0, .5, 1.0]]))
        array([[0. , 0.5, 1. ]], dtype=float32)
        >>> ensure_float01(np.array([[0, 1, 200]]))
        array([[0..., 0.0039..., 0.784...]], dtype=float32)
    """
    if img.dtype.kind in ('i', 'u'):
        assert img.max() <= 255
        img_ = img.astype(dtype, copy=copy) / 255.0
    else:
        img_ = img.astype(dtype, copy=copy)
    return img_


def ensure_uint255(img, copy=True):
    """
    Ensure that an image is encoded using a uint8 properly

    Args:
        img (ndarray): an image in uint255 or float01 format.
            Other formats will raise errors.

    Example:
        >>> ensure_uint255(np.array([[0, .5, 1.0]]))
        array([[  0, 127, 255]], dtype=uint8)
        >>> ensure_uint255(np.array([[0, 1, 200]]))
        array([[  0,   1, 200]], dtype=uint8)
    """
    if img.dtype.kind == 'u':
        img_ = img.astype(np.uint8, copy=copy)
    elif img.dtype.kind == 'i':
        assert img.min() >= 0 and img.max() <= 255
        img_ = img.astype(np.uint8, copy=copy)
    else:
        # If the image is a float check that it is between 0 and 1 and then
        # convert.
        assert img.min() >= 0 and img.max() <= 1
        img_ = (img * 255).astype(np.uint8, copy=copy)
    return img_


# def _cast_type(*arrs):
#     # SeeAlso: np.common_type
#     for a in arrs:
#         if a.dtype.kind in ['f']:
#             pass


def make_channels_comparable(img1, img2, atleast3d=False):
    """
    Broadcasts image arrays so they can have elementwise operations applied

    Example:
        >>> import itertools as it
        >>> wh_basis = [(5, 5), (3, 5), (5, 3), (1, 1), (1, 3), (3, 1)]
        >>> for w, h in wh_basis:
        >>>     shape_basis = [(w, h), (w, h, 1), (w, h, 3)]
        >>>     # Test all permutations of shap inputs
        >>>     for shape1, shape2 in it.product(shape_basis, shape_basis):
        >>>         print('*    input shapes: %r, %r' % (shape1, shape2))
        >>>         img1 = np.empty(shape1)
        >>>         img2 = np.empty(shape2)
        >>>         img1, img2 = make_channels_comparable(img1, img2)
        >>>         print('... output shapes: %r, %r' % (img1.shape, img2.shape))
        >>>         elem = (img1 + img2)
        >>>         print('... elem(+) shape: %r' % (elem.shape,))
        >>>         assert elem.size == img1.size, 'outputs should have same size'
        >>>         assert img1.size == img2.size, 'new imgs should have same size'
        >>>         print('--------')
    """
    # if img1.shape[0:2] != img2.shape[0:2]:
    #     raise ValueError(
    #         'Spatial sizes of {!r} and {!r} are not compatible'.format(
    #             img1.shape, img2.shape))
    if img1.shape != img2.shape:
        c1 = num_channels(img1)
        c2 = num_channels(img2)
        if len(img1.shape) == 2 and len(img2.shape) == 2:
            # Both images are 2d grayscale
            if atleast3d:
                # add the third dim with 1 channel
                img1 = img1[:, None]
                img2 = img2[:, None]
        elif len(img1.shape) == 3 and len(img2.shape) == 2:
            # Image 2 is grayscale
            if c1 == 3:
                img2 = np.tile(img2[..., None], 3)
            else:
                img2 = img2[..., None]
        elif len(img1.shape) == 2 and len(img2.shape) == 3:
            # Image 1 is grayscale
            if c2 == 3:
                img1 = np.tile(img1[..., None], 3)
            else:
                img1 = img1[..., None]
        elif len(img1.shape) == 3 and len(img2.shape) == 3:
            # Both images have 3 dims.
            # Check if either have color, then check for alpha
            if c1 == 1 and c2 == 1:
                # raise AssertionError('UNREACHABLE: Both are 3-grayscale')
                pass
            elif c1 == 4 and c2 == 4:
                # raise AssertionError('UNREACHABLE: Both are 3-alpha')
                pass
            elif c1 == 3 and c2 == 3:
                # raise AssertionError('UNREACHABLE: Both are 3-color')
                pass
            elif c1 == 1 and c2 == 3:
                img1 = np.tile(img1, 3)
            elif c1 == 3 and c2 == 1:
                img2 = np.tile(img2, 3)
            elif c1 == 1 and c2  == 4:
                img1 = np.dstack((np.tile(img1, 3), _alpha_fill_for(img1)))
            elif c1 == 4 and c2  == 1:
                img2 = np.dstack((np.tile(img2, 3), _alpha_fill_for(img2)))
            elif c1 == 3 and c2  == 4:
                img1 = np.dstack((img1, _alpha_fill_for(img1)))
            elif c1 == 4 and c2  == 3:
                img2 = np.dstack((img2, _alpha_fill_for(img2)))
            else:
                raise AssertionError('Unknown shape case: %r, %r' % (img1.shape, img2.shape))
        else:
            raise AssertionError('Unknown shape case: %r, %r' % (img1.shape, img2.shape))
    return img1, img2


def _alpha_fill_for(img):
    """ helper for make_channels_comparable """
    fill_value = (255 if img.dtype.kind in ('i', 'u') else 1)
    alpha_chan = np.full(img.shape[0:2], dtype=img.dtype,
                         fill_value=fill_value)
    return alpha_chan


def atleast_3channels(arr, copy=True):
    r"""
    Ensures that there are 3 channels in the image

    Args:
        arr (ndarray[N, M, ...]): the image
        copy (bool): Always copies if True, if False, then copies only when the
            size of the array must change.

    Returns:
        ndarray: with shape (N, M, C), where C in {3, 4}

    Doctest:
        >>> assert atleast_3channels(np.zeros((10, 10))).shape[-1] == 3
        >>> assert atleast_3channels(np.zeros((10, 10, 1))).shape[-1] == 3
        >>> assert atleast_3channels(np.zeros((10, 10, 3))).shape[-1] == 3
        >>> assert atleast_3channels(np.zeros((10, 10, 4))).shape[-1] == 4
    """
    ndims = len(arr.shape)
    if ndims == 2:
        res = np.tile(arr[:, :, None], 3)
        return res
    elif ndims == 3:
        h, w, c = arr.shape
        if c == 1:
            res = np.tile(arr, 3)
        elif c in [3, 4]:
            res = arr.copy() if copy else arr
        else:
            raise ValueError('Cannot handle ndims={}'.format(ndims))
    else:
        raise ValueError('Cannot handle arr.shape={}'.format(arr.shape))
    return res