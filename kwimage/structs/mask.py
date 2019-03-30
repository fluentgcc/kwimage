"""
Data structure for Binary Masks

Structure for efficient encoding of per-annotation segmentation masks
Based on efficient cython/C code in the cocoapi [1].

References:
    ..[1] https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/_mask.pyx
    ..[2] https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.c
    ..[3] https://github.com/nightrome/cocostuffapi/blob/master/common/maskApi.h
    ..[4] https://github.com/nightrome/cocostuffapi/blob/master/PythonAPI/pycocotools/mask.py

Goals:
    The goal of this file is to create a datastructure that lets the developer
    seemlessly convert between:
        (1) raw binary uint8 masks
        (2) memory-efficient comprsssed run-length-encodings of binary
        segmentation masks.
        (3) convex polygons
        (4) convex hull polygons
        (5) bounding box

    It is not there yet, and the API is subject to change in order to better
    accomplish these goals.

Notes:
    IN THIS FILE ONLY: size corresponds to a h/w tuple to be compatible with
    the coco semantics. Everywhere else in this repo, size uses opencv
    semantics which are w/h.

"""
import cv2
import copy
import six
import numpy as np
import ubelt as ub
import itertools as it
from . import _generic
from kwimage.structs._mask_backend import cython_mask
import xdev

__all__ = ['Mask', 'MaskList']


class MaskFormat:
    """
    Defines valid formats and their aliases.

    Attrs:
        aliases (Mapping[str, str]):
            maps format aliases to their cannonical name.
    """
    cannonical = []

    def _register(k, cannonical=cannonical):
        cannonical.append(k)
        return k

    BYTES_RLE = _register('bytes_rle')  # cython compressed RLE
    ARRAY_RLE = _register('array_rle')  # numpy uncompreesed RLE
    C_MASK    = _register('c_mask')     # row-major raw binary mask
    F_MASK    = _register('f_mask')     # column-major raw binary mask

    aliases = {
    }
    for key in cannonical:
        aliases[key] = key


class _MaskConversionMixin(object):
    """
    Mixin class registering conversion functions

    For conversion speeds look into:
        ~/code/kwimage/dev/bench_rle.py
    """
    convert_funcs = {}

    def _register_convertor(key, convert_funcs=convert_funcs):
        def _reg(func):
            convert_funcs[key] = func
            return func
        return _reg

    def toformat(self, format, copy=False):
        """
        Changes the internal representation using one of the registered
        convertor functions.

        Args:
            format (str):
                the string code for the format you want to transform into.

        Example:
            >>> from kwimage.structs.mask import MaskFormat  # NOQA
            >>> mask = Mask.random(shape=(8, 8), rng=0)
            >>> # Test that we can convert to and from all formats
            >>> for format1 in MaskFormat.cannonical:
            ...     mask1 = mask.toformat(format1)
            ...     for format2 in MaskFormat.cannonical:
            ...         mask2 = mask1.toformat(format2)
            ...         img1 = mask1.to_c_mask().data
            ...         img2 = mask2.to_c_mask().data
            ...         if not np.all(img1 == img2):
            ...             msg = 'Failed convert {} <-> {}'.format(format1, format2)
            ...             print(msg)
            ...             raise AssertionError(msg)
            ...         else:
            ...             msg = 'Passed convert {} <-> {}'.format(format1, format2)
            ...             print(msg)
        """
        key = MaskFormat.aliases.get(format, format)
        try:
            func = self.convert_funcs[key]
            return func(self, copy)
        except KeyError:
            raise KeyError('Cannot convert {} to {}'.format(self.format, format))

    @_register_convertor(MaskFormat.BYTES_RLE)
    def to_bytes_rle(self, copy=False):
        """
        Example:
            >>> from kwimage.structs.mask import MaskFormat  # NOQA
            >>> mask = Mask.demo()
            >>> print(mask.to_bytes_rle().data['counts'])
            ...'_153L;4EL;1DO10;1DO10;1DO10;4EL;4ELW3b0jL^O60...
            >>> print(mask.to_array_rle().data['counts'].tolist())
            [47, 5, 3, 1, 14, 5, 3, 1, 14, 2, 2, 1, 3, 1, 14, ...
            >>> print(mask.to_array_rle().to_bytes_rle().data['counts'])
            ...'_153L;4EL;1DO10;1DO10;1DO10;4EL;4ELW3b0jL^O60L0...
        """
        if self.format == MaskFormat.BYTES_RLE:
            return self.copy() if copy else self
        if self.format == MaskFormat.ARRAY_RLE:
            h, w = self.data['size']
            if self.data.get('order', 'F') != 'F':
                raise ValueError('Expected column-major array RLE')
            newdata = cython_mask.frUncompressedRLE([self.data], h, w)[0]
            self = Mask(newdata, MaskFormat.BYTES_RLE)

        elif self.format == MaskFormat.F_MASK:
            f_masks = self.data[:, :, None]
            encoded = cython_mask.encode(f_masks)[0]
            self = Mask(encoded, format=MaskFormat.BYTES_RLE)
        elif self.format == MaskFormat.C_MASK:
            c_mask = self.data
            f_masks = np.asfortranarray(c_mask)[:, :, None]
            encoded = cython_mask.encode(f_masks)[0]
            self = Mask(encoded, format=MaskFormat.BYTES_RLE)
        else:
            raise NotImplementedError(self.format)
        return self

    @_register_convertor(MaskFormat.ARRAY_RLE)
    def to_array_rle(self, copy=False):
        if self.format == MaskFormat.ARRAY_RLE:
            return self.copy() if copy else self
        elif self.format == MaskFormat.BYTES_RLE:
            from kwimage.im_runlen import _rle_bytes_to_array
            arr_counts = _rle_bytes_to_array(self.data['counts'])
            encoded = {
                'size': self.data['size'],
                'binary': self.data.get('binary', True),
                'counts': arr_counts,
                'order': self.data.get('order', 'F'),
            }
            encoded['shape'] = self.data.get('shape', encoded['size'])
            self = Mask(encoded, format=MaskFormat.ARRAY_RLE)
        else:
            import kwimage
            f_mask = self.to_fortran_mask().data
            encoded = kwimage.encode_run_length(f_mask, binary=True, order='F')
            encoded['size'] = encoded['shape']  # hack in size
            self = Mask(encoded, format=MaskFormat.ARRAY_RLE)
        return self

    @_register_convertor(MaskFormat.F_MASK)
    def to_fortran_mask(self, copy=False):
        if self.format == MaskFormat.F_MASK:
            return self.copy() if copy else self
        elif self.format == MaskFormat.C_MASK:
            c_mask = self.data.copy() if copy else self.data
            f_mask = np.asfortranarray(c_mask)
        elif self.format == MaskFormat.ARRAY_RLE:
            import kwimage
            encoded = dict(self.data)
            encoded.pop('size', None)
            f_mask = kwimage.decode_run_length(**encoded)
        else:
            # NOTE: inefficient, could be improved
            self = self.to_bytes_rle(copy=False)
            f_mask = cython_mask.decode([self.data])[:, :, 0]
        self = Mask(f_mask, MaskFormat.F_MASK)
        return self

    @_register_convertor(MaskFormat.C_MASK)
    def to_c_mask(self, copy=False):
        if self.format == MaskFormat.C_MASK:
            return self.copy() if copy else self
        elif self.format == MaskFormat.F_MASK:
            f_mask = self.data.copy() if copy else self.data
            c_mask = np.ascontiguousarray(f_mask)
        else:
            f_mask = self.to_fortran_mask(copy=False).data
            c_mask = np.ascontiguousarray(f_mask)
        self = Mask(c_mask, MaskFormat.C_MASK)
        return self


class _MaskConstructorMixin(object):
    """
    Alternative ways to construct a masks object
    """

    @classmethod
    def from_polygons(Mask, polygons, dims):
        """
        DEPRICATE:

        Args:
            polygons (ndarray | List[ndarray]): one or more polygons that
                will be joined together. The ndarray may either be an
                Nx2 or a flat c-contiguous array or xy points.
            dims (Tuple): height / width of the source image

        Example:
            >>> polygons = [
            >>>     np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]]),
            >>>     np.array([[0, 9],[4, 8],[2, 3]]),
            >>> ]
            >>> dims = (9, 5)
            >>> self = Mask.from_polygons(polygons, dims)
            >>> print(self)
            <Mask({'counts': b'724;MG2MN16', 'size': [9, 5]}, format=bytes_rle)>
            >>> polygon = polygons[0]
            >>> print(Mask.from_polygons(polygon, shape))
            <Mask({'counts': b'b04500N2', 'size': [9, 5]}, format=bytes_rle)>
        """
        h, w = dims
        # TODO: holes? geojson?
        if isinstance(polygons, np.ndarray):
            polygons = [polygons]
        flat_polys = [np.array(ps).ravel() for ps in polygons]
        encoded = cython_mask.frPoly(flat_polys, h, w)
        ccs = [Mask(e, MaskFormat.BYTES_RLE) for e in encoded]
        self = Mask.union(*ccs)
        return self

    @classmethod
    def from_mask(Mask, mask, offset=None, shape=None, method='faster'):
        """
        Creates an RLE encoded mask from a raw binary mask, but you may
        optionally specify an offset if the mask is part of a larger image.

        Args:
            mask (ndarray):
                a binary submask which belongs to a larger image

            offset (Tuple[int, int]):
                top-left xy location of the mask in the larger image

            shape (Tuple[int, int]): shape of the larger image

        SeeAlso:
            ../../test/test_rle.py

        Example:
            >>> mask = Mask.random(shape=(32, 32), rng=0).data
            >>> offset = (30, 100)
            >>> shape = (501, 502)
            >>> self = Mask.from_mask(mask, offset=offset, shape=shape, method='faster')
        """
        if shape is None:
            shape = mask.shape
        if offset is None:
            offset = (0, 0)
        if method == 'naive':
            # inefficent but used to test correctness of algorithms
            import kwimage
            rc_offset = offset[::-1]
            larger = kwimage.subpixel_translate(mask, rc_offset,
                                                output_shape=shape)
            # larger = np.zeros(shape, dtype=mask.dtype)
            # larger_rc = offset[::-1]
            # mask_dims = mask.shape[0:2]
            # index = tuple(slice(s, s + d) for s, d in zip(larger_rc, mask_dims))
            # larger[index] = mask
            self = Mask(larger, MaskFormat.C_MASK).to_array_rle()
        elif method == 'faster':
            import kwimage
            encoded = kwimage.encode_run_length(mask, binary=True, order='F')
            encoded['size'] = encoded['shape']
            self = Mask(encoded, MaskFormat.ARRAY_RLE)
            self = self.translate(offset, shape)
        else:
            raise KeyError(method)
        return self


class _MaskTransformMixin(object):

    @xdev.profile
    def scale(self, factor, output_dims=None, inplace=False):
        """
        Example:
            >>> self = Mask.random()
            >>> factor = 5
            >>> inplace = False
            >>> new = self.scale(factor)
            >>> print('new.shape = {!r}'.format(new.shape))
        """
        if not ub.iterable(factor):
            sx = sy = factor
        else:
            sx, sy = factor
        if output_dims is None:
            output_dims = (np.array(self.shape) * np.array((sy, sx))).astype(np.int)
        # FIXME: the warp breaks when the third row is left out
        transform = np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0, 0, 1]])
        new = self.warp(transform, output_dims=output_dims, inplace=inplace)
        return new

    @xdev.profile
    def warp(self, transform, input_dims=None, output_dims=None, inplace=False):
        """

        Example:
            >>> import kwimage
            >>> self = mask = kwimage.Mask.random()
            >>> transform = np.array([[5., 0, 0], [0, 5, 0], [0, 0, 1]])
            >>> output_dims = np.array(self.shape) * 6
            >>> new = self.warp(transform, output_dims=output_dims)
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> kwplot.figure(fnum=1, pnum=(1, 2, 1))
            >>> self.draw()
            >>> kwplot.figure(fnum=1, pnum=(1, 2, 2))
            >>> new.draw()
        """
        # HACK: use brute force just to get this implemented.
        # very inefficient
        import kwimage
        import torch
        c_mask = self.to_c_mask(copy=False).data

        t_mask = torch.Tensor(c_mask)
        matrix = torch.Tensor(transform)
        output_dims = output_dims
        w_mask = kwimage.warp_tensor(t_mask, matrix, output_dims=output_dims,
                                     mode='nearest')
        new = self if inplace else Mask(self.data, self.format)
        new.data = w_mask.numpy().astype(np.uint8)
        new.format = MaskFormat.C_MASK
        return new

    @xdev.profile
    def translate(self, offset, output_dims=None):
        """
        Efficiently translate an array_rle in the encoding space

        Args:
            offset (Tuple): x,y offset
            output_dims (Tuple, optional): h,w of transformed mask.
                If unspecified the parent shape is used.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> shape = (10, 10)
            >>> offset = (1, 1)
            >>> data2 = self.translate(offset, shape).to_c_mask().data
            >>> assert np.all(data2[1:7, 1:7] == self.data[:6, :6])
        """
        import kwimage
        if output_dims is None:
            output_dims = self.shape
        if not ub.iterable(offset):
            offset = (offset, offset)
        rle = self.to_array_rle(copy=False).data
        new_rle = kwimage.rle_translate(rle, offset, output_dims)
        new_rle['size'] = new_rle['shape']
        new_self = Mask(new_rle, MaskFormat.ARRAY_RLE)
        return new_self


class _MaskDrawMixin(object):
    """
    Non-core functions for mask visualization
    """

    def draw_on(self, image, color='blue', alpha=0.5,
                show_border=False, border_thick=1,
                border_color='white'):
        """
        Draws the mask on an image

        Example:
            >>> # xdoc: +REQUIRES(module:kwplot)
            >>> from kwimage.structs.mask import *  # NOQA
            >>> import kwimage
            >>> image = kwimage.grab_test_image()
            >>> self = Mask.random(shape=image.shape[0:2])
            >>> toshow = self.draw_on(image)
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> kwplot.imshow(toshow)
            >>> kwplot.show_if_requested()
        """
        import kwplot
        import kwimage

        mask = self.to_c_mask().data
        rgb01 = list(kwplot.Color(color).as01())
        rgba01 = np.array(rgb01 + [1])[None, None, :]
        alpha_mask = rgba01 * mask[:, :, None]
        alpha_mask[..., 3] = mask * alpha

        toshow = kwimage.overlay_alpha_images(alpha_mask, image)

        if show_border:
            # return shape of contours to openCV contours
            contours = [np.expand_dims(c, axis=1) for c in self.get_polygon()]
            toshow = cv2.drawContours((toshow * 255.).astype(np.uint8),
                                      contours, -1,
                                      kwplot.Color(border_color).as255(),
                                      border_thick, cv2.LINE_AA)
            toshow = toshow.astype(np.float) / 255.

        return toshow

    def draw(self, color='blue', alpha=0.5, ax=None, show_border=False,
             border_thick=1, border_color='black'):
        """
        Draw on the current matplotlib axis
        """
        import kwplot
        if ax is None:
            from matplotlib import pyplot as plt
            ax = plt.gca()

        mask = self.to_c_mask().data
        rgb01 = list(kwplot.Color(color).as01())
        rgba01 = np.array(rgb01 + [1])[None, None, :]
        alpha_mask = rgba01 * mask[:, :, None]
        alpha_mask[..., 3] = mask * alpha

        if show_border:
            # Add alpha channel to color
            border_color_tup = kwplot.Color(border_color).as255()
            border_color_tup = (border_color_tup[0], border_color_tup[1],
                                border_color_tup[2], 255 * alpha)

            # return shape of contours to openCV contours
            contours = [np.expand_dims(c, axis=1) for c in self.get_polygon()]
            alpha_mask = cv2.drawContours((alpha_mask * 255.).astype(np.uint8), contours, -1,
                                          border_color_tup, border_thick, cv2.LINE_AA)

            alpha_mask = alpha_mask.astype(np.float) / 255.

        ax.imshow(alpha_mask)


class Mask(ub.NiceRepr, _MaskConversionMixin, _MaskConstructorMixin,
           _MaskTransformMixin, _MaskDrawMixin):
    """
    Manages a single segmentation mask and can convert to and from
    multiple formats including:

        * bytes_rle
        * array_rle
        * c_mask
        * f_mask

    Example:
        >>> # a ms-coco style compressed bytes rle segmentation
        >>> segmentation = {'size': [5, 9], 'counts': ';?1B10O30O4'}
        >>> mask = Mask(segmentation, 'bytes_rle')
        >>> # convert to binary numpy representation
        >>> binary_mask = mask.to_c_mask().data
        >>> print(ub.repr2(binary_mask.tolist(), nl=1, nobr=1))
        [0, 0, 0, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 0, 0, 0],
        [0, 0, 1, 1, 1, 1, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 1, 1, 0],
        [0, 0, 1, 1, 1, 0, 1, 1, 0],

    """
    def __init__(self, data=None, format=None):
        self.data = data
        self.format = format

    def __nice__(self):
        return '{}, format={}'.format(ub.repr2(self.data, nl=0), self.format)

    # def tensor(self):
    #     # self.
    #     # Mask(item.to_bytes_rle
    #     # pass

    @classmethod
    def random(Mask, rng=None, shape=(32, 32)):
        import kwarray
        import kwimage
        rng = kwarray.ensure_rng(rng)
        # Use random heatmap to make some blobs for the mask
        probs = kwimage.Heatmap.random(
            dims=shape, rng=rng, classes=2).data['class_probs'][1]
        c_mask = (probs > .5).astype(np.uint8)
        self = Mask(c_mask, MaskFormat.C_MASK)
        return self

    def copy(self):
        """
        Performs a deep copy of the mask data

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> other = self.copy()
            >>> assert other.data is not self.data
        """
        return Mask(copy.deepcopy(self.data), self.format)

    def union(self, *others):
        """
        This can be used as a staticmethod or an instancemethod

        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> masks = [Mask.random(shape=(8, 8), rng=i) for i in range(2)]
            >>> mask = Mask.union(*masks)
            >>> print(mask.area)
            34
            >>> masks = [m.to_c_mask() for m in masks]
            >>> mask = Mask.union(*masks)
            >>> print(mask.area)

            >>> masks = [m.to_bytes_rle() for m in masks]
            >>> mask = Mask.union(*masks)
            >>> print(mask.area)

        Benchmark:
            import ubelt as ub
            ti = ub.Timerit(100, bestof=10, verbose=2)

            masks = [Mask.random(shape=(172, 172), rng=i) for i in range(2)]

            for timer in ti.reset('native rle union'):
                masks = [m.to_bytes_rle() for m in masks]
                with timer:
                    mask = Mask.union(*masks)

            for timer in ti.reset('native cmask union'):
                masks = [m.to_c_mask() for m in masks]
                with timer:
                    mask = Mask.union(*masks)

            for timer in ti.reset('cmask->rle union'):
                masks = [m.to_c_mask() for m in masks]
                with timer:
                    mask = Mask.union(*[m.to_bytes_rle() for m in masks])
        """
        if isinstance(self, Mask):
            cls = self.__class__
            items = list(it.chain([self], others))
        else:
            cls = Mask
            items = others

        if len(items) == 0:
            raise Exception('empty union')
        else:
            format = items[0].format
            if format == MaskFormat.C_MASK:
                datas = [item.to_c_mask().data for item in items]
                new_data = np.bitwise_or.reduce(datas)
                new = cls(new_data, MaskFormat.C_MASK)
            elif format == MaskFormat.BYTES_RLE:
                datas = [item.to_bytes_rle().data for item in items]
                new_data = cython_mask.merge(datas, intersect=0)
                new = cls(new_data, MaskFormat.BYTES_RLE)
            else:
                datas = [item.to_bytes_rle().data for item in items]
                new_rle = cython_mask.merge(datas, intersect=0)
                new = cls(new_rle, MaskFormat.BYTES_RLE)
        return new
        # rle_datas = [item.to_bytes_rle().data for item in items]
        # return cls(cython_mask.merge(rle_datas, intersect=0), MaskFormat.BYTES_RLE)

    def intersection(self, *others):
        """
        This can be used as a staticmethod or an instancemethod

        Example:
            >>> masks = [Mask.random(shape=(8, 8), rng=i) for i in range(2)]
            >>> mask = Mask.intersection(*masks)
            >>> print(mask.area)
            8
        """
        cls = self.__class__ if isinstance(self, Mask) else Mask
        rle_datas = [item.to_bytes_rle().data for item in it.chain([self], others)]
        return cls(cython_mask.merge(rle_datas, intersect=1), MaskFormat.BYTES_RLE)

    @property
    def shape(self):
        if self.format in {MaskFormat.BYTES_RLE, MaskFormat.ARRAY_RLE}:
            if 'shape' in self.data:
                return self.data['shape']
            else:
                return self.data['size']
        if self.format in {MaskFormat.C_MASK, MaskFormat.F_MASK}:
            return self.data.shape

    @property
    def area(self):
        """
        Returns the number of non-zero pixels

        Example:
            >>> self = Mask.demo()
            >>> self.area
            150
        """
        self = self.to_bytes_rle()
        return cython_mask.area([self.data])[0]

    def get_patch(self):
        """
        Extract the patch with non-zero data

        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> self.get_patch()
            array([[0, 0, 1, 0, 0, 0, 0, 0],
                   [1, 1, 1, 1, 0, 0, 0, 0],
                   [1, 1, 1, 0, 0, 0, 0, 0],
                   [0, 0, 0, 0, 0, 0, 1, 1]], dtype=uint8)
        """
        x, y, w, h = self.get_xywh().astype(np.int).tolist()
        output_dims = (h, w)
        xy_offset = (-x, -y)
        temp = self.translate(xy_offset, output_dims)
        patch = temp.to_c_mask().data
        return patch

    def get_xywh(self):
        """
        Gets the bounding xywh box coordinates of this mask

        Returns:
            ndarray: x, y, w, h: Note we dont use a Boxes object because
                a general singular version does not yet exist.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> self.get_xywh().tolist()
            [0.0, 1.0, 8.0, 4.0]
            >>> self = Mask.random(rng=0).translate((10, 10))
            >>> self.get_xywh().tolist()
        """
        # import kwimage
        self = self.to_bytes_rle()
        xywh = cython_mask.toBbox([self.data])[0]
        # boxes = kwimage.Boxes(xywh, 'xywh')
        # return boxes
        return xywh

    def get_polygon(self):
        """
        Returns a list of (x,y)-coordinate lists. The length of the list is
        equal to the number of disjoint regions in the mask.

        Returns:
            List[ndarray]: polygon around each connected component of the
                mask. Each ndarray is an Nx2 array of xy points.

        NOTE:
            The returned polygon may not surround points that are only one
            pixel thick.

        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> polygons = self.get_polygon()
            >>> print('polygons = ' + ub.repr2(polygons))
            >>> polygons = self.get_polygon()
            >>> self = self.to_bytes_rle()
            >>> other = Mask.from_polygons(polygons, self.shape)
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> image = np.ones(self.shape)
            >>> image = self.draw_on(image, color='blue')
            >>> image = other.draw_on(image, color='red')
            >>> kwplot.imshow(image)

            polygons = [
                np.array([[6, 4],[7, 4]], dtype=np.int32),
                np.array([[0, 1],[0, 3],[2, 3],[2, 1]], dtype=np.int32),
            ]
        """
        import warnings
        warnings.warn('depricated use to_multi_polygon', DeprecationWarning)
        p = 2

        if 0:
            mask = self.to_c_mask().data
            offset = (-p, -p)
        else:
            # It should be faster to only exact the patch of non-zero values
            x, y, w, h = self.get_xywh().astype(np.int).tolist()
            output_dims = (h, w)
            xy_offset = (-x, -y)
            temp = self.translate(xy_offset, output_dims)
            mask = temp.to_c_mask().data
            offset = (x - p, y - p)

        padded_mask = cv2.copyMakeBorder(mask, p, p, p, p,
                                         cv2.BORDER_CONSTANT, value=0)

        # print('src =\n{!r}'.format(padded_mask))
        kernel = np.array([
            [1, 1, 0],
            [1, 1, 0],
            [0, 0, 0],
        ], dtype=np.uint8)
        padded_mask = cv2.dilate(padded_mask, kernel, dst=padded_mask)
        # print('dst =\n{!r}'.format(padded_mask))

        mode = cv2.RETR_LIST
        # mode = cv2.RETR_EXTERNAL

        # https://docs.opencv.org/3.1.0/d3/dc0/
        # group__imgproc__shape.html#ga4303f45752694956374734a03c54d5ff

        method = cv2.CHAIN_APPROX_SIMPLE
        # method = cv2.CHAIN_APPROX_NONE
        # method = cv2.CHAIN_APPROX_TC89_KCOS
        # Different versions of cv2 have different return types
        _ret = cv2.findContours(padded_mask, mode, method, offset=offset)
        if len(_ret) == 2:
            _contours, _hierarchy = _ret
        else:
            _img, _contours, _hierarchy = _ret

        polygon = [c[:, 0, :] for c in _contours]

        # TODO: a kwimage structure for polygons

        if False:
            import kwil
            kwil.autompl()
            # Note that cv2 draw contours doesnt have the 1-pixel thick problem
            # it seems to just be the way the coco implementation is
            # interpreting polygons.
            image = kwil.atleast_3channels(mask)
            toshow = np.zeros(image.shape, dtype="uint8")
            cv2.drawContours(toshow, _contours, -1, (255, 0, 0), 1)
            kwil.imshow(toshow)

        return polygon

    def to_mask(self):
        return self

    @classmethod
    def demo(cls):
        """
        Demo mask with holes and disjoint shapes
        """
        text = ub.codeblock(
            '''
            ................................
            ..ooooooo....ooooooooooooo......
            ..ooooooo....o...........o......
            ..oo...oo....o.oooooooo..o......
            ..oo...oo....o.o......o..o......
            ..ooooooo....o.o..oo..o..o......
            .............o.o...o..o..o......
            .............o.o..oo..o..o......
            .............o.o......o..o......
            ..ooooooo....o.oooooooo..o......
            .............o...........o......
            .............o...........o......
            .............ooooooooooooo......
            .............o...........o......
            .............o...........o......
            .............o....ooooo..o......
            .............o....o...o..o......
            .............o....ooooo..o......
            .............o...........o......
            .............ooooooooooooo......
            ................................
            ................................
            ................................
            ''')
        lines = text.split('\n')
        data = [[0 if c == '.' else 1 for c in line] for line in lines]
        data = np.array(data).astype(np.uint8)
        self = cls(data, format=MaskFormat.C_MASK)
        return self

    def to_multi_polygon(self):
        """
        Returns a MultiPolygon object fit around this raster including disjoint
        pieces and holes.

        Returns:
            MultiPolygon: vectorized representation

        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> self = Mask.demo()
            >>> self = self.scale(5)
            >>> multi_poly = self.to_multi_polygon()
            >>> self.draw(color='red')
            >>> multi_poly.scale(1.1).draw(color='blue')

            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> image = np.ones(self.shape)
            >>> image = self.draw_on(image, color='blue')
            >>> #image = other.draw_on(image, color='red')
            >>> kwplot.imshow(image)
            >>> multi_poly.draw()
        """
        import cv2
        p = 2
        # It should be faster to only exact the patch of non-zero values
        x, y, w, h = self.get_xywh().astype(np.int).tolist()
        if w > 0 and h > 0:
            output_dims = (h, w)
            xy_offset = (-x, -y)
            temp = self.translate(xy_offset, output_dims)
            mask = temp.to_c_mask().data
            offset = (x - p, y - p)

            padded_mask = cv2.copyMakeBorder(mask, p, p, p, p,
                                             cv2.BORDER_CONSTANT, value=0)

            # https://docs.opencv.org/3.1.0/d3/dc0/
            # group__imgproc__shape.html#ga4303f45752694956374734a03c54d5ff
            mode = cv2.RETR_CCOMP
            method = cv2.CHAIN_APPROX_SIMPLE
            # method = cv2.CHAIN_APPROX_TC89_KCOS
            # Different versions of cv2 have different return types
            _ret = cv2.findContours(padded_mask, mode, method, offset=offset)
            if len(_ret) == 2:
                _contours, _hierarchy = _ret
            else:
                _img, _contours, _hierarchy = _ret
            _hierarchy = _hierarchy[0]

            polys = {i: {'exterior': None, 'interiors': []}
                     for i, row in enumerate(_hierarchy) if row[3] == -1}
            for i, row in enumerate(_hierarchy):
                # This only works in RETR_CCOMP mode
                nxt, prev, child, parent = row[0:4]
                if parent != -1:
                    polys[parent]['interiors'].append(_contours[i][:, 0, :])
                else:
                    polys[i]['exterior'] = _contours[i][:, 0, :]

            from kwimage.structs.polygon import Polygon, MultiPolygon
            poly_list = [Polygon(**data) for data in polys.values()]
            multi_poly = MultiPolygon(poly_list)
        else:
            from kwimage.structs.polygon import Polygon, MultiPolygon
            multi_poly = MultiPolygon([])
        return multi_poly

        # if False:
        #     import kwil
        #     kwil.autompl()
        #     # Note that cv2 draw contours doesnt have the 1-pixel thick problem
        #     # it seems to just be the way the coco implementation is
        #     # interpreting polygons.

        #     from matplotlib.patches import Path
        #     from matplotlib import pyplot as plt
        #     import matplotlib as mpl

        #     kwil.imshow(self.to_c_mask().data, fnum=2, doclf=True)
        #     ax = plt.gca()
        #     patches = []

        #     for i, poly in polys.items():
        #         exterior = poly['exterior'].tolist()
        #         exterior.append(exterior[0])
        #         n = len(exterior)
        #         verts = []
        #         verts.extend(exterior)
        #         codes = [Path.MOVETO] + ([Path.LINETO] * (n - 2)) + [Path.CLOSEPOLY]

        #         interiors = poly['interiors']
        #         for hole in interiors:
        #             hole = hole.tolist()
        #             hole.append(hole[0])
        #             n = len(hole)
        #             verts.extend(hole)
        #             codes += [Path.MOVETO] + ([Path.LINETO] * (n - 2)) + [Path.CLOSEPOLY]

        #         verts = np.array(verts)
        #         path = Path(verts, codes)
        #         patch = mpl.patches.PathPatch(path)
        #         patches.append(patch)
        #     poly_col = mpl.collections.PatchCollection(patches, 2, alpha=0.4)
        #     ax.add_collection(poly_col)
        #     ax.set_xlim(0, 32)
        #     ax.set_ylim(0, 32)

        #     # line_type = cv2.LINE_AA
        #     # line_type = cv2.LINE_4
        #     line_type = cv2.LINE_8
        #     contour_idx = -1
        #     thickness = 1
        #     toshow = np.zeros(self.shape, dtype="uint8")
        #     toshow = kwil.atleast_3channels(toshow)
        #     toshow = cv2.drawContours(toshow, _contours, contour_idx, (255, 0, 0), thickness, line_type)
        #     kwil.imshow(toshow, fnum=2, doclf=True)

        # return polygon

    def get_convex_hull(self):
        """
        Returns a list of xy points around the convex hull of this mask

        NOTE:
            The returned polygon may not surround points that are only one
            pixel thick.

        Example:
            >>> self = Mask.random(shape=(8, 8), rng=0)
            >>> polygons = self.get_convex_hull()
            >>> print('polygons = ' + ub.repr2(polygons))
            >>> other = Mask.from_polygons(polygons, self.shape)
        """
        mask = self.to_c_mask().data
        cc_y, cc_x = np.where(mask)
        points = np.vstack([cc_x, cc_y]).T
        hull = cv2.convexHull(points)[:, 0, :]
        return hull

    def iou(self, other):
        """
        The area of intersection over the area of union

        TODO:
            - [ ] Write plural Masks version of this class, which should
                  be able to perform this operation more efficiently.

        CommandLine:
            xdoctest -m kwimage.structs.mask Mask.iou

        Example:
            >>> self = Mask.demo()
            >>> other = self.translate(1)
            >>> iou = self.iou(other)
            >>> print('iou = {:.4f}'.format(iou))
            iou = 0.0830
        """
        item1 = self.to_bytes_rle(copy=False).data
        item2 = other.to_bytes_rle(copy=False).data
        # I'm not sure what passing `pyiscrowd` actually does here
        # TODO: determine what `pyiscrowd` does, and document it.
        pyiscrowd = np.array([0], dtype=np.uint8)
        iou = cython_mask.iou([item1], [item2], pyiscrowd)[0, 0]
        return iou

    @classmethod
    def coerce(Mask, data, dims=None):
        """
        Attempts to auto-inspect the format of the data and conver to Mask

        Args:
            data : the data to coerce
            dims (Tuple): required for certain formats like polygons
                height / width of the source image

        Returns:
            Mask

        Example:
            >>> segmentation = {'size': [5, 9], 'counts': ';?1B10O30O4'}
            >>> polygon = [
            >>>     [np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]])],
            >>>     [np.array([[2, 1],[2, 2],[4, 2],[4, 1]])],
            >>> ]
            >>> dims = (9, 5)
            >>> mask = (np.random.rand(32, 32) > .5).astype(np.uint8)
            >>> Mask.coerce(polygon, dims).to_bytes_rle()
            >>> Mask.coerce(segmentation).to_bytes_rle()
            >>> Mask.coerce(mask).to_bytes_rle()
        """
        self = _coerce_coco_segmentation(data, dims)
        self = self.to_mask(dims)
        return self

    def _to_coco(self):
        """
        Example:
            >>> from kwimage.structs.mask import *  # NOQA
            >>> self = Mask.demo()
            >>> data = self._to_coco()
            >>> print(ub.repr2(data, nl=1))
        """
        if False:
            data = self.to_bytes_rle().data.copy()
            if six.PY3:
                data['counts'] = ub.ensure_unicode(data['counts'])
            else:
                data['counts'] = data['counts']
        else:
            data = self.to_array_rle().data.copy()
            data['counts'] = data['counts'].tolist()
        return data


def _coerce_coco_segmentation(data, dims=None):
    """
    Attempts to auto-inspect the format of segmentation data

    Args:
        data : the data to coerce

             2D-C-ndarray -> C_MASK
             2D-F-ndarray -> F_MASK

             Dict(counts=bytes) -> BYTES_RLE
             Dict(counts=ndarray) -> ARRAY_RLE

             Dict(exterior=ndarray) -> ARRAY_RLE

             # List[List[int]] -> Polygon
             List[int] -> Polygon
             List[Dict] -> MultPolygon

        dims (Tuple): required for certain formats like polygons
            height / width of the source image

    Returns:
        Mask | Polygon | MultiPolygon - depending on which is appropriate

    Example:
        >>> segmentation = {'size': [5, 9], 'counts': ';?1B10O30O4'}
        >>> dims = (9, 5)
        >>> raw_mask = (np.random.rand(32, 32) > .5).astype(np.uint8)
        >>> _coerce_coco_segmentation(segmentation)
        >>> _coerce_coco_segmentation(raw_mask)

        >>> coco_polygon = [
        >>>     np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]]),
        >>>     np.array([[2, 1],[2, 2],[4, 2],[4, 1]]),
        >>> ]
        >>> self = _coerce_coco_segmentation(coco_polygon, dims)
        >>> print('self = {!r}'.format(self))
        >>> coco_polygon = [
        >>>     np.array([[3, 0],[2, 1],[2, 4],[4, 4],[4, 3],[7, 0]]),
        >>> ]
        >>> self = _coerce_coco_segmentation(coco_polygon, dims)
        >>> print('self = {!r}'.format(self))
    """
    import kwimage
    if isinstance(data, np.ndarray):
        # INPUT TYPE: RAW MASK
        if dims is not None:
            assert dims == data.shape[0:2]
        if data.flags['F_CONTIGUOUS']:
            self = kwimage.Mask(data, MaskFormat.F_MASK)
        else:
            self = kwimage.Mask(data, MaskFormat.C_MASK)
    elif isinstance(data, dict):
        if 'counts' in data:
            # INPUT TYPE: COCO RLE DICTIONARY
            if dims is not None:
                data_shape = data.get('dims', data.get('shape', data.get('size', None)))
                if data_shape is None:
                    data['shape'] = data_shape
                else:
                    assert tuple(map(int, dims)) == tuple(map(int, data_shape)), (
                        '{} {}'.format(dims, data_shape))
            if isinstance(data['counts'], (six.text_type, six.binary_type)):
                self = kwimage.Mask(data, MaskFormat.BYTES_RLE)
            else:
                self = kwimage.Mask(data, MaskFormat.ARRAY_RLE)
        elif 'exterior' in data:
            raise NotImplementedError('explicit polygon coerce')
        else:
            raise TypeError
    elif isinstance(data, list):
        # THIS IS NOT AN IDEAL FORMAT. IDEALLY WE WILL MODIFY COCO TO USE
        # DICTIONARIES FOR POLYGONS, WHICH ARE UNAMBIGUOUS
        if len(data) == 0:
            self = None
        else:
            first = ub.peek(data)
            if isinstance(first, dict):
                raise NotImplementedError('MultiPolygon')
            elif isinstance(first, int):
                exterior = np.array(data).reshape(-1, 2)
                self = kwimage.Polygon(exterior=exterior)
            elif isinstance(first, list):
                poly_list = [kwimage.Polygon(exterior=np.array(item).reshape(-1, 2))
                             for item in data]
                if len(poly_list) == 1:
                    self = poly_list[0]
                else:
                    self = kwimage.MultiPolygon(poly_list)
            elif isinstance(first, np.ndarray):
                poly_list = [kwimage.Polygon(exterior=item.reshape(-1, 2))
                             for item in data]
                if len(poly_list) == 1:
                    self = poly_list[0]
                else:
                    self = kwimage.MultiPolygon(poly_list)
            else:
                raise TypeError
    else:
        raise TypeError
    return self


class MaskList(_generic.ObjectList):
    """
    Store and manipulate multiple masks, usually within the same image
    """


if __name__ == '__main__':
    """
    CommandLine:
        xdoctest -m kwimage.structs.mask all
    """
    import xdoctest
    xdoctest.doctest_module(__file__)