# -*- coding: utf-8 -*-
"""
Structure for efficient access and modification of bounding boxes with
associated scores and class labels. Builds on top of the `kwimage.Boxes`
structure.

Also can optionally incorporate `kwimage.PolygonList` for segmentation masks
and `kwimage.PointsList` for keypoints.
"""
from __future__ import absolute_import, division, print_function, unicode_literals
import six
import torch
import numpy as np
import ubelt as ub
import xdev
from kwimage.structs import boxes as _boxes
from kwimage.structs import _generic


class _DetDrawMixin:
    """
    Non critical methods for visualizing detections
    """
    def draw(self, color='blue', alpha=None, labels=True, centers=False, lw=2,
             fill=False, ax=None, radius=5, kpts=True, sseg=True,
             setlim=False):
        """
        Draws boxes using matplotlib

        Example:
            >>> # xdoc: +REQUIRES(module:kwplot)
            >>> self = Detections.random(num=10, scale=512.0, rng=0, classes=['a', 'b', 'c'])
            >>> self.boxes.translate((-128, -128), inplace=True)
            >>> image = (np.random.rand(256, 256) * 255).astype(np.uint8)
            >>> # xdoc: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> fig = kwplot.figure(fnum=1, doclf=True)
            >>> kwplot.imshow(image)
            >>> # xdoc: -REQUIRES(--show)
            >>> self.draw(color='blue', alpha=None)
            >>> # xdoc: +REQUIRES(--show)
            >>> for o in fig.findobj():  # http://matplotlib.1069221.n5.nabble.com/How-to-turn-off-all-clipping-td1813.html
            >>>     o.set_clip_on(False)
            >>> kwplot.show_if_requested()
        """
        segmentations = self.data.get('segmentations', None)
        if sseg and segmentations is not None:
            segmentations.draw(color=color, alpha=.4)

        labels = self._make_labels(labels)
        alpha = self._make_alpha(alpha)
        self.boxes.draw(labels=labels, color=color, alpha=alpha, fill=fill,
                        centers=centers, ax=ax, lw=lw)

        keypoints = self.data.get('keypoints', None)
        if kpts and keypoints is not None:
            keypoints.draw(color=color, radius=radius)

        if setlim:
            x1, y1, x2, y2 = self.boxes.to_tlbr().components
            xmax = x2.max()
            xmin = x1.min()
            ymax = y2.max()
            ymin = y1.min()
            import matplotlib.pyplot as plt
            ax = plt.gca()
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)

    @xdev.profile
    def draw_on(self, image, color='blue', alpha=None, labels=True, radius=5,
                kpts=True, sseg=True):
        """
        Draws boxes directly on the image using OpenCV

        Args:
            image (ndarray[uint8]): must be in uint8 format

        Returns:
            ndarray[uint8]: image with labeled boxes drawn on it

        CommandLine:
            xdoctest -m kwimage.structs.detections _DetDrawMixin.draw_on:1 --profile --show

        Example:
            >>> # xdoc: +REQUIRES(module:kwplot)
            >>> import kwplot
            >>> self = Detections.random(num=10, scale=512, rng=0)
            >>> image = (np.random.rand(512, 512) * 255).astype(np.uint8)
            >>> image2 = self.draw_on(image, color='blue')
            >>> # xdoc: +REQUIRES(--show)
            >>> kwplot.figure(fnum=2000, doclf=True)
            >>> kwplot.autompl()
            >>> kwplot.imshow(image2)
            >>> kwplot.show_if_requested()

        Example:
            >>> # xdoc: +REQUIRES(module:kwplot)
            >>> # xdoc: +REQUIRES(--profile)
            >>> import kwplot
            >>> self = Detections.random(num=100, scale=512, rng=0, keypoints=True, segmentations=True)
            >>> image = (np.random.rand(512, 512) * 255).astype(np.uint8)
            >>> image2 = self.draw_on(image, color='blue')
            >>> # xdoc: +REQUIRES(--show)
            >>> kwplot.figure(fnum=2000, doclf=True)
            >>> kwplot.autompl()
            >>> kwplot.imshow(image2)
            >>> kwplot.show_if_requested()
        """
        labels = self._make_labels(labels)
        alpha = self._make_alpha(alpha)
        # import kwimage

        dtype_fixer = _generic._consistent_dtype_fixer(image)

        segmentations = self.data.get('segmentations', None)
        if sseg and segmentations is not None:
            # image = kwimage.ensure_uint255(image)
            image = segmentations.draw_on(image, color=color, alpha=.4)
            # kwimage.ensure_float01(image)

        image = self.boxes.draw_on(image, color=color, alpha=alpha,
                                   labels=labels)

        keypoints = self.data.get('keypoints', None)
        if kpts and keypoints is not None:
            # image = kwimage.ensure_float01(image)
            image = keypoints.draw_on(image, radius=radius, color=color)
            # kwimage.ensure_float01(image)

        image = dtype_fixer(image)
        return image

    def _make_alpha(self, alpha):
        """
        Either passes through user specified alpha or chooses a sensible
        default
        """
        if alpha in ['score', 'scores']:
            alpha = np.sqrt(self.scores)
        else:
            if alpha is None or alpha is False:
                alpha = 1.0
            alpha = [float(alpha)] * self.num_boxes()
        return alpha

    def _make_labels(self, labels):
        """
        Either passes through user specified labels or chooses a sensible
        default
        """
        if labels:
            if labels is True:
                parts = []
                if self.data.get('class_idxs', None) is not None:
                    parts.append('class')
                # Choose sensible default
                if self.data.get('scores', None) is not None:
                    parts.append('score')
                labels = '+'.join(parts)

            if isinstance(labels, six.string_types):
                if labels in ['class', 'class+score']:
                    if self.classes:
                        identifers = list(ub.take(self.classes, self.class_idxs))
                    else:
                        identifers = self.class_idxs
                if labels in ['class']:
                    labels = identifers
                elif labels in ['score']:
                    labels = ['{:.4f}'.format(score) for score in self.scores]
                elif labels in ['class+score']:
                    labels = ['{} @ {:.4f}'.format(cid, score)
                              for cid, score in zip(identifers, self.scores)]
                else:
                    raise KeyError('unknown labels key {!r}'.format(labels))
        return labels


class _DetAlgoMixin:
    """
    Non critical methods for algorithmic manipulation of detections
    """

    def non_max_supression(self, thresh=0.0, perclass=False, impl='auto',
                           daq=False):
        """
        Find high scoring minimally overlapping detections

        Args:
            thresh (float): iou threshold
            perclass (bool): if True, works on a per-class basis
            impl (str): nms implementation to use
            daq (Bool | Dict): if False, uses reqgular nms, otherwise uses
                divide and conquor algorithm. If `daq` is a Dict, then
                it is used as the kwargs to `kwimage.daq_spatial_nms`

        Returns:
            ndarray[int]: indices of boxes to keep
        """
        import kwimage
        classes = self.class_idxs if perclass else None
        tlbr = self.boxes.to_tlbr().data
        scores = self.data.get('scores', None)
        if scores is None:
            scores = np.ones(len(self), dtype=np.float32)
        if daq:
            daqkw = {} if daq is True else daq.copy()
            daqkw['impl'] = daqkw.get('impl', impl)
            daqkw['stop_size'] = daqkw.get('stop_size', 2048)
            daqkw['max_depth'] = daqkw.get('max_depth', 12)
            daqkw['thresh'] = daqkw.get('thresh', thresh)
            if 'diameter' not in daqkw:
                daqkw['diameter'] = max(self.boxes.width.max(),
                                        self.boxes.height.max())

            keep = kwimage.daq_spatial_nms(tlbr, scores, **daqkw)
        else:
            keep = kwimage.non_max_supression(tlbr, scores, thresh=thresh,
                                              classes=classes, impl=impl)
        return keep

    def non_max_supress(self, thresh=0.0, perclass=False, impl='auto',
                        daq=False):
        """
        Convinience method. Like `non_max_supression`, but returns to supressed
        boxes instead of the indices to keep.
        """
        keep = self.non_max_supression(thresh=thresh, perclass=perclass,
                                       impl=impl, daq=daq)
        return self.take(keep)

    def rasterize(self, bg_size, input_dims, soften=1, tf_data_to_img=None, img_dims=None):
        """
        Ambiguous conversion from a Heatmap to a Detections object.

        SeeAlso:
            Heatmap.detect

        Returns:
            kwimage.Heatmap: raster-space detections.

        Example:
            >>> # xdoctest: +REQUIRES(module:ndsampler)
            >>> from kwimage.structs.detections import *  # NOQA
            >>> self, iminfo, sampler = Detections.demo()
            >>> image = iminfo['imdata']
            >>> input_dims = iminfo['imdata'].shape[0:2]
            >>> bg_size = [100, 100]
            >>> heatmap = self.rasterize(bg_size, input_dims)
            >>> # xdoctest: +REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> kwplot.figure(fnum=1, pnum=(2, 2, 1))
            >>> heatmap.draw(invert=True)
            >>> kwplot.figure(fnum=1, pnum=(2, 2, 2))
            >>> kwplot.imshow(heatmap.draw_on(image))
            >>> kwplot.figure(fnum=1, pnum=(2, 1, 2))
            >>> kwplot.imshow(heatmap.draw_stacked())
        """
        import kwarray
        import skimage
        import kwimage
        classes = self.meta['classes']

        bg_idx = classes.index('background')
        fcn_target = _dets_to_fcmaps(self, bg_size=bg_size,
                                     input_dims=input_dims, bg_idx=bg_idx,
                                     soft=False)

        if tf_data_to_img is None:
            tf_data_to_img = skimage.transform.AffineTransform(
                scale=(1, 1), translation=(0, 0),
            )

        if img_dims is None:
            img_dims = np.array(input_dims)
        # print(fcn_target.keys())
        # print('fcn_target: ' + ub.repr2(ub.map_vals(lambda x: x.shape, fcn_target), nl=1))

        impl = kwarray.ArrayAPI.coerce(fcn_target['cidx'])

        # class_probs = nh.criterions.focal.one_hot_embedding(
        #     fcn_target['cidx'].reshape(-1),
        #     num_classes=len(classes), dim=1)
        labels = fcn_target['cidx']
        class_probs = kwarray.one_hot_embedding(
            labels, num_classes=len(classes), dim=0)
        # if 0:
        #     kwil.imshow(fcn_target['cidx'] > 0)
        #     kwil.imshow(class_probs[0])

        if soften > 0:
            k = 31
            sigma = 0.3 * ((k - 1) * 0.5 - 1) + 0.8  # opencv formula
            data = impl.contiguous(class_probs.T)
            import cv2
            cv2.GaussianBlur(data, (k, k), sigma, dst=data)
            class_probs = impl.contiguous(data.T)

        if soften > 1:
            class_probs = impl.softmax(class_probs, axis=0)

        dims = tuple(class_probs.shape[1:])

        kw_heat = {
            'diameter': fcn_target['size'][[1, 0]],
            'offset': fcn_target['dxdy'][[1, 0]],

            'class_idx': fcn_target['cidx'],
            'class_probs': class_probs,

            'img_dims': img_dims,
            'tf_data_to_img': tf_data_to_img,
            'datakeys': ['kpts_ignore', 'class_idx'],
        }

        if 'kpts' in fcn_target:
            kp_classes = self.meta['kp_classes']
            K = len(kp_classes)
            # TODO: add noise or do some bluring?
            kw_heat['keypoints'] = impl.view(fcn_target['kpts'], (2, K,) + dims)[[1, 0]]
            kw_heat['kpts_ignore'] = fcn_target['kpts_ignore']

        self = kwimage.Heatmap(**kw_heat)
        # print('self.data: ' + ub.repr2(ub.map_vals(lambda x: x.shape, self.data), nl=1))
        return self


class Detections(ub.NiceRepr, _DetAlgoMixin, _DetDrawMixin):
    """
    Container for holding and manipulating multiple detections.

    Attributes:
        data (Dict): dictionary containing corresponding lists. The length of
            each list is the number of detections. This contains the bounding
            boxes, confidence scores, and class indices. Details of the most
            common keys and types are as follows:

                boxes (kwimage.Boxes[ArrayLike]): multiple bounding boxes
                scores (ArrayLike): associated scores
                class_idxs (ArrayLike): associated class indices

            Additional custom keys may be specified as long as (a) the values
            are array-like and the first axis corresponds to the standard data
            values and (b) are custom keys are listed in the `datakeys` kwargs
            when constructing the Detections.

        meta (Dict):
            This contains contextual information about the detections.  This
            includes the class names, which can be indexed into via the class
            indexes.

    Example:
        >>> self = Detections.random(10)
        >>> other = Detections(self)
        >>> assert other.data == self.data
        >>> assert other.data is self.data, 'try not to copy unless necessary'
    """
    # __slots__ = ('data', 'meta',)

    # Valid keys for the data dictionary
    # NOTE: I'm not sure its productive to restrict to a set of specified
    # properties. It might be better to allow detections to have arbitrary data
    # properties like: velocity, as long as they are array-like. However, I'm
    # not sure how to best structure the code to allow this so it is both clear
    # and efficient. Currently I've allowed the user to specify custom datakeys
    # and metakeys as kwargs, but that design might change.
    __datakeys__ = ['boxes', 'scores', 'class_idxs', 'probs', 'weights',
                    'keypoints', 'segmentations']

    # Valid keys for the meta dictionary
    __metakeys__ = ['classes']

    def __init__(self, data=None, meta=None, datakeys=None, metakeys=None,
                 checks=True, **kwargs):
        """
        Construct a Detections object by either explicitly specifying the
        internal data and meta dictionary structures or by passing expected
        attribute names as kwargs. Note that custom data and metadata can be
        specified as long as you pass the names of these keys in the `datakeys`
        and/or `metakeys` kwargs.

        Args:
            data (Dict[str, ArrayLike]): explicitly specify the data dictionary
            meta (Dict[str, object]): explicitly specify the meta dictionary
            datakeys (List[str]): a list of custom attributes that should be
               considered as data (i.e. must be an array aligned with boxes).
            metakeys (List[str]): a list of custom attributes that should be
               considered as metadata (i.e. can be arbitrary).
            checks (bool, default=True): if True and arguments are passed by
                kwargs, then check / ensure that all types are compatible
            **kwargs:
                specify any key for the data or meta dictionaries.

        Example:
            >>> import kwimage
            >>> dets = kwimage.Detections(
            >>>     # there are expected keys that do not need registration
            >>>     boxes=kwimage.Boxes.random(3),
            >>>     class_idxs=[0, 1, 1],
            >>>     classes=['a', 'b'],
            >>>     # custom data attrs must align with boxes
            >>>     myattr1=np.random.rand(3),
            >>>     myattr2=np.random.rand(3, 2, 8),
            >>>     # there are no restrictions on metadata
            >>>     mymeta='a custom metadata string',
            >>>     # Note that any key not in kwimage.Detections.__datakeys__ or
            >>>     # kwimage.Detections.__metakeys__ must be registered at the
            >>>     # time of construction.
            >>>     datakeys=['myattr1', 'myattr2'],
            >>>     metakeys=['mymeta'],
            >>>     checks=True,
            >>> )

        Doctest:
            >>> # TODO: move to external unit test
            >>> # Coerce to numpy
            >>> import kwimage
            >>> dets = Detections(
            >>>     boxes=kwimage.Boxes.random(3).numpy(),
            >>>     class_idxs=[0, 1, 1],
            >>>     checks=True,
            >>> )
            >>> # Coerce to tensor
            >>> dets = Detections(
            >>>     boxes=kwimage.Boxes.random(3).tensor(),
            >>>     class_idxs=[0, 1, 1],
            >>>     checks=True,
            >>> )
            >>> # Error on incompatible types
            >>> import pytest
            >>> with pytest.raises(TypeError):
            >>>     dets = Detections(
            >>>         boxes=kwimage.Boxes.random(3).tensor(),
            >>>         scores=np.random.rand(3),
            >>>         class_idxs=[0, 1, 1],
            >>>         checks=True,
            >>>     )
        """
        # Standardize input format
        if kwargs:
            if data or meta:
                raise ValueError('Cannot specify kwargs AND data/meta dicts')
            _datakeys = self.__datakeys__
            _metakeys = self.__metakeys__
            # Allow the user to specify custom data and meta keys
            if datakeys is not None:
                _datakeys = _datakeys + list(datakeys)
            if metakeys is not None:
                _metakeys = _metakeys + list(metakeys)
            # Perform input checks whenever kwargs is given
            data = {key: kwargs.pop(key) for key in _datakeys if key in kwargs}
            meta = {key: kwargs.pop(key) for key in _metakeys if key in kwargs}
            if kwargs:
                raise ValueError(
                    'Unknown kwargs: {}'.format(sorted(kwargs.keys())))

            if checks:
                import kwarray
                # Check to make sure all types in `data` are compatible
                ndarrays = []
                tensors = []
                other = []
                objlist = []
                for k, v in data.items():
                    if isinstance(v, _generic.ObjectList):
                        objlist.append(v)
                    elif isinstance(v, _boxes.Boxes):
                        if v.is_numpy():
                            ndarrays.append(k)
                        else:
                            tensors.append(k)
                    elif isinstance(v, np.ndarray):
                        ndarrays.append(k)
                    elif isinstance(v, torch.Tensor):
                        tensors.append(k)
                    else:
                        other.append(k)

                if bool(ndarrays) and bool(tensors):
                    raise TypeError(
                        'Detections can hold numpy.ndarrays or torch.Tensors, '
                        'but not both')
                if tensors:
                    impl = kwarray.ArrayAPI.coerce('tensor')
                else:
                    impl = kwarray.ArrayAPI.coerce('numpy')
                for k in other:
                    data[k] = impl.asarray(data[k])

        elif isinstance(data, self.__class__):
            # Avoid runtime checks and assume the user is doing the right thing
            # if data and meta are explicitly specified
            meta = data.meta
            data = data.data
        if meta is None:
            meta = {}

        self.data = data
        self.meta = meta

    def __nice__(self):
        return self.num_boxes()

    def __len__(self):
        return self.num_boxes()

    def copy(self):
        """
        Returns a deep copy of this Detections object
        """
        import copy
        return copy.deepcopy(self)

    @classmethod
    def from_coco_annots(cls, anns, cats=None, classes=None, kp_classes=None,
                         shape=None, dset=None):
        """
        Args:
            anns (List[Dict]): list of coco-like annotation objects
            shape (tuple): shape of parent image
            dset (CocoDataset): if specified, cats, classes, and kp_classes
                can are ignored.

        Example:
            >>> from kwimage.structs.detections import *  # NOQA
            >>> # xdoctest: +REQUIRES(--module:ndsampler)
            >>> anns = [{
            >>>     'id': 0,
            >>>     'image_id': 1,
            >>>     'category_id': 2,
            >>>     'bbox': [2, 3, 10, 10],
            >>>     'keypoints': [4.5, 4.5, 2],
            >>>     'segmentation': {
            >>>         'counts': '_11a04M2O0O20N101N3L_5',
            >>>         'size': [20, 20],
            >>>     },
            >>> }]
            >>> dataset = {
            >>>     'images': [],
            >>>     'annotations': [],
            >>>     'categories': [
            >>>         {'id': 0, 'name': 'background'},
            >>>         {'id': 2, 'name': 'class1', 'keypoints': ['spot']}
            >>>     ]
            >>> }
            >>> #import ndsampler
            >>> #dset = ndsampler.CocoDataset(dataset)
            >>> cats = dataset['categories']
            >>> dets = Detections.from_coco_annots(anns, cats)

        Example:
            >>> import kwimage
            >>> # xdoctest: +REQUIRES(--module:ndsampler)
            >>> import ndsampler
            >>> sampler = ndsampler.CocoSampler.demo('photos')
            >>> iminfo, anns = sampler.load_image_with_annots(1)
            >>> shape = iminfo['imdata'].shape[0:2]
            >>> kp_classes = sampler.dset.keypoint_categories()
            >>> dets = kwimage.Detections.from_coco_annots(
            >>>     anns, sampler.dset.dataset['categories'], sampler.catgraph,
            >>>     kp_classes, shape=shape)

        Ignore:
            import skimage
            m = skimage.morphology.disk(4)
            mask = kwimage.Mask.from_mask(m, offset=(2, 3), shape=(20, 20))
            print(mask.to_bytes_rle().data)
        """

        cnames = None
        if dset is not None:
            cats = dset.dataset['categories']
            kp_classes = dset.keypoint_categories()
        else:
            if cats is None:
                cnames = []
                for ann in anns:
                    if 'category_name' in ann:
                        cnames.append(ann['category_name'])
                    else:
                        raise Exception('Specify dset or cats or category_name in each annotation')
                if classes is None:
                    classes = sorted(set(cnames))
                assert set(cnames).issubset(set(classes))

                # make dummy cats
                cats = [{'name': name, 'id': cid}
                        for cid, name in enumerate(classes, start=1) ]

        if classes is None:
            classes = list(ub.oset([cat['name'] for cat in cats]))

        if cnames is None:
            cids = [ann['category_id'] for ann in anns]
            cid_to_cat = {c['id']: c for c in cats}  # Hack
            cnames = [cid_to_cat[cid]['name'] for cid in cids]

        import kwimage
        xywh = np.array([ann['bbox'] for ann in anns], dtype=np.float32)
        boxes = kwimage.Boxes(xywh, 'xywh')
        class_idxs = [classes.index(cname) for cname in cnames]

        dets = Detections(
            boxes=boxes,
            class_idxs=np.array(class_idxs),
            classes=classes,
        )
        if True:
            ss = [ann.get('segmentation', None) for ann in anns]
            masks = [
                None if s is None else
                kwimage.MultiPolygon.coerce(s, dims=shape)
                for s in ss
            ]
            dets.data['segmentations'] = kwimage.PolygonList(masks)

        if True:
            name_to_cat = {c['name']: c for c in cats}
            def _lookup_kp_class_idxs(cid):
                kpnames = None
                while kpnames is None:
                    cat = cid_to_cat[cid]
                    parent = cat.get('supercategory', None)
                    if 'keypoints' in cat:
                        kpnames = cat['keypoints']
                    elif parent is not None:
                        cid = name_to_cat[cat['supercategory']]['id']
                    else:
                        raise KeyError(cid)
                kpcidxs = [kp_classes.index(n) for n in kpnames]
                return kpcidxs
            kpts = []
            for ann in anns:
                k = ann.get('keypoints', None)
                if k is None:
                    kpts.append(k)
                else:
                    kpcidxs = None
                    if kp_classes is not None:
                        kpcidxs = _lookup_kp_class_idxs(ann['category_id'])
                    xy = np.array(k).reshape(-1, 3)[:, 0:2]
                    pts = kwimage.Points(
                        xy=xy,
                        class_idxs=kpcidxs,
                    )
                    kpts.append(pts)
            dets.data['keypoints'] = kwimage.PointsList(kpts)

            if kp_classes is not None:
                dets.data['keypoints'].meta['classes'] = kp_classes
                dets.meta['kp_classes'] = kp_classes
        return dets

    def to_coco(self, cname_to_cat=None):
        """
        CommandLine:
            xdoctest -m kwimage.structs.detections Detections.to_coco

        Example:
            >>> from kwimage.structs.detections import *
            >>> self = Detections.demo()[0]
            >>> cname_to_cat = None
            >>> list(self.to_coco())
        """
        to_collate = {}
        if 'boxes' in self.data:
            to_collate['bbox'] = list(self.data['boxes'].to_coco())

        if 'class_idxs' in self.data:
            if 'classes' in self.meta:
                classes = self.meta['classes']
                catnames = [classes[cidx] for cidx in self.class_idxs]
                if cname_to_cat is not None:
                    pass
                to_collate['category_name'] = catnames
            else:
                to_collate['category_index'] = self.data['class_idxs']

        if 'keypoints' in self.data:
            to_collate['keypoints'] = list(self.data['keypoints'].to_coco())

        if 'segmentations' in self.data:
            to_collate['segmentation'] = list(self.data['segmentations'].to_coco())

        # coco_extra_keys = ['scores', 'weights', 'probs']
        # for key in coco_extra_keys:
        #     if key in self.data:
        #         to_collate[key] = self.data[key].tolist()

        keys = list(to_collate.keys())
        # annotations = []
        for item_vals in zip(*to_collate.values()):
            ann = ub.dzip(keys, item_vals)
            yield ann
            # annotations.append(ann)
        # return annotations

    # --- Data Properties ---

    @property
    def boxes(self):
        return self.data['boxes']

    @property
    def class_idxs(self):
        return self.data['class_idxs']

    @property
    def scores(self):
        """ typically only populated for predicted detections """
        return self.data['scores']

    @property
    def probs(self):
        """ typically only populated for predicted detections """
        return self.data['probs']

    @property
    def weights(self):
        """ typically only populated for groundtruth detections """
        return self.data['weights']

    # --- Meta Properties ---

    @property
    def classes(self):
        return self.meta.get('classes', None)

    def num_boxes(self):
        return len(self.boxes)

    # --- Modifiers ---

    @xdev.profile
    def warp(self, transform, input_dims=None, output_dims=None, inplace=False):
        """
        Spatially warp the detections.

        Example:
            >>> import skimage
            >>> transform = skimage.transform.AffineTransform(scale=(2, 3), translation=(4, 5))
            >>> self = Detections.random(2)
            >>> new = self.warp(transform)
            >>> assert new.boxes == self.boxes.warp(transform)
            >>> assert new != self
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        new.data['boxes'] = new.data['boxes'].warp(transform, inplace=inplace)
        if 'keypoints' in new.data:
            new.data['keypoints'] = new.data['keypoints'].warp(
                transform, input_dims=input_dims, output_dims=output_dims,
                inplace=inplace)
        if 'segmentations' in new.data:
            new.data['segmentations'] = new.data['segmentations'].warp(
                transform, input_dims=input_dims, output_dims=output_dims,
                inplace=inplace)
        return new

    @xdev.profile
    def scale(self, factor, output_dims=None, inplace=False):
        """
        Spatially warp the detections.

        Example:
            >>> import skimage
            >>> transform = skimage.transform.AffineTransform(scale=(2, 3), translation=(4, 5))
            >>> self = Detections.random(2)
            >>> new = self.warp(transform)
            >>> assert new.boxes == self.boxes.warp(transform)
            >>> assert new != self
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        new.data['boxes'] = new.data['boxes'].scale(factor, inplace=inplace)
        if 'keypoints' in new.data:
            new.data['keypoints'] = new.data['keypoints'].scale(
                factor, output_dims=output_dims, inplace=inplace)
        if 'segmentations' in new.data:
            new.data['segmentations'] = new.data['segmentations'].scale(
                factor, output_dims=output_dims, inplace=inplace)
        return new

    @xdev.profile
    def translate(self, offset, output_dims=None, inplace=False):
        """
        Spatially warp the detections.

        Example:
            >>> import skimage
            >>> self = Detections.random(2)
            >>> new = self.translate(10)
        """
        new = self if inplace else self.__class__(self.data.copy(), self.meta)
        new.data['boxes'] = new.data['boxes'].translate(offset, inplace=inplace)
        if 'keypoints' in new.data:
            new.data['keypoints'] = new.data['keypoints'].translate(
                offset, output_dims=output_dims)
        if 'segmentations' in new.data:
            new.data['segmentations'] = new.data['segmentations'].translate(
                offset, output_dims=output_dims)
        return new

    @classmethod
    def concatenate(cls, dets):
        """
        Args:
            boxes (Sequence[Detections]): list of detections to concatenate

        Returns:
            Detections: stacked detections

        Example:
            >>> self = Detections.random(2)
            >>> other = Detections.random(3)
            >>> dets = [self, other]
            >>> new = Detections.concatenate(dets)
            >>> assert new.num_boxes() == 5
        """
        if len(dets) == 0:
            raise ValueError('need at least one detection to concatenate')
        newdata = {}
        first = dets[0]
        for key in first.data.keys():
            if first.data[key] is None:
                newdata[key] = None
            else:
                try:
                    tocat = [d.data[key] for d in dets]
                    try:
                        # Use class concatenate if it exists,
                        cat = tocat[0].__class__.concatenate
                    except AttributeError:
                        # otherwise use numpy/torch
                        cat = _boxes._cat
                    newdata[key] = cat(tocat, axis=0)
                except Exception:
                    msg = ('Error when trying to concat {}'.format(key))
                    print(msg)
                    raise
                    raise Exception(msg)

        newmeta = dets[0].meta
        new = cls(newdata, newmeta)
        return new

    def argsort(self, reverse=True):
        """
        Sorts detection indices by descending (or ascending) scores

        Returns:
            ndarray[int]: sorted indices
        """
        sortx = self.scores.argsort()
        if reverse:
            sortx = sortx[::-1]
        return sortx

    def sort(self, reverse=True):
        """
        Sorts detections by descending (or ascending) scores

        Returns:
            kwimage.structs.Detections: sorted copy of self
        """
        sortx = self.argsort(reverse=reverse)
        return self.take(sortx)

    def compress(self, flags, axis=0):
        """
        Returns a subset where corresponding locations are True.

        Args:
            flags (ndarray[bool]): mask marking selected items

        Returns:
            kwimage.structs.Detections: subset of self

        CommandLine:
            xdoctest -m kwimage.structs.detections Detections.compress

        Example:
            >>> import kwimage
            >>> dets = kwimage.Detections.random(keypoints='dense')
            >>> flags = np.random.rand(len(dets)) > 0.5
            >>> subset = dets.compress(flags)
            >>> assert len(subset) == flags.sum()
            >>> subset = dets.tensor().compress(flags)
            >>> assert len(subset) == flags.sum()


            z = dets.tensor().data['keypoints'].data['xy']
            z.compress(flags)
            ub.map_vals(lambda x: x.shape, dets.data)
            ub.map_vals(lambda x: x.shape, subset.data)

        """
        if flags is Ellipsis:
            return self

        if len(flags) != len(self):
            raise IndexError('compress must get a flag for every item')

        if self.is_tensor():
            if isinstance(flags, np.ndarray):
                if flags.dtype.kind == 'b':
                    flags = flags.astype(np.uint8)
            flags = torch.ByteTensor(flags).to(self.device)
        newdata = {k: _generic._safe_compress(v, flags, axis)
                   for k, v in self.data.items()}
        return self.__class__(newdata, self.meta)

    def take(self, indices, axis=0):
        """
        Returns a subset specified by indices

        Args:
            indices (ndarray[int]): indices to select

        Returns:
            kwimage.structs.Detections: subset of self

        Example:
            >>> import kwimage
            >>> dets = kwimage.Detections(boxes=kwimage.Boxes.random(10))
            >>> subset = dets.take([2, 3, 5, 7])
            >>> assert len(subset) == 4
            >>> subset = dets.tensor().take([2, 3, 5, 7])
            >>> assert len(subset) == 4
        """
        if self.is_tensor():
            indices = torch.LongTensor(indices).to(self.device)
        newdata = {k: _generic._safe_take(v, indices, axis)
                   for k, v in self.data.items()}
        return self.__class__(newdata, self.meta)

    def __getitem__(self, index):
        """
        Fancy slicing / subset / indexing.

        Note: scalar indices are always coerced into index lists of length 1.

        Example:
            >>> import kwimage
            >>> import kwarray
            >>> dets = kwimage.Detections(boxes=kwimage.Boxes.random(10))
            >>> indices = [2, 3, 5, 7]
            >>> flags = kwarray.boolmask(indices, len(dets))
            >>> assert dets[flags].data == dets[indices].data
        """
        if isinstance(index, slice):
            index = list(range(*index.indices(len(self))))
        if ub.iterable(index):
            import kwarray
            impl = kwarray.ArrayAPI.coerce('numpy')
            indices = impl.asarray(index)
        else:
            indices = np.array([index])
        if indices.dtype.kind == 'b':
            return self.compress(indices)
        else:
            return self.take(indices)

    @property
    def device(self):
        """ If the backend is torch returns the data device, otherwise None """
        return self.boxes.device

    def is_tensor(self):
        """ is the backend fueled by torch? """
        return self.boxes.is_tensor()

    def is_numpy(self):
        """ is the backend fueled by numpy? """
        return self.boxes.is_numpy()

    @xdev.profile
    def numpy(self):
        """
        Converts tensors to numpy. Does not change memory if possible.

        Example:
            >>> self = Detections.random(3).tensor()
            >>> newself = self.numpy()
            >>> self.scores[0] = 0
            >>> assert newself.scores[0] == 0
            >>> self.scores[0] = 1
            >>> assert self.scores[0] == 1
            >>> self.numpy().numpy()
        """
        newdata = {}
        for key, val in self.data.items():
            if val is None:
                newval = val
            else:
                if torch.is_tensor(val):
                    newval = val.data.cpu().numpy()
                elif hasattr(val, 'numpy'):
                    newval = val.numpy()
                else:
                    newval = val
            newdata[key] = newval
        newself = self.__class__(newdata, self.meta)
        return newself

    @xdev.profile
    def tensor(self, device=ub.NoParam):
        """
        Converts numpy to tensors. Does not change memory if possible.

        Example:
            >>> from kwimage.structs.detections import *
            >>> self = Detections.random(3)
            >>> newself = self.tensor()
            >>> self.scores[0] = 0
            >>> assert newself.scores[0] == 0
            >>> self.scores[0] = 1
            >>> assert self.scores[0] == 1
            >>> self.tensor().tensor()
        """
        newdata = {}
        for key, val in self.data.items():
            if val is None:
                newval = val
            elif hasattr(val, 'tensor'):
                newval = val.tensor(device)
            else:
                if torch.is_tensor(val):
                    newval = val
                else:
                    newval = torch.from_numpy(val)
                if device is not ub.NoParam:
                    newval = newval.to(device)
            newdata[key] = newval
        newself = self.__class__(newdata, self.meta)
        return newself

    # --- Non-core methods ----

    @classmethod
    def demo(Detections):
        import ndsampler
        sampler = ndsampler.CocoSampler.demo('photos')
        iminfo, anns = sampler.load_image_with_annots(1)
        input_dims = iminfo['imdata'].shape[0:2]
        kp_classes = sampler.dset.keypoint_categories()
        self = Detections.from_coco_annots(
            anns, sampler.dset.dataset['categories'],
            sampler.catgraph, kp_classes, shape=input_dims)
        return self, iminfo, sampler

    @classmethod
    def random(cls, num=10, scale=1.0, rng=None, classes=3, keypoints=False,
               tensor=False, segmentations=False):
        """
        Creates dummy data, suitable for use in tests and benchmarks

        Args:
            num (int): number of boxes
            scale (float | tuple, default=1.0): bounding image size
            classes (int | Sequence): list of class labels or number of classes
            tensor (bool, default=False): determines backend
            rng (np.random.RandomState): random state

        Example:
            >>> import kwimage
            >>> dets = kwimage.Detections.random(keypoints='jagged')
            >>> dets.data['keypoints'].data[0].data
            >>> dets.data['keypoints'].meta
            >>> dets = kwimage.Detections.random(keypoints='dense')
            >>> dets = kwimage.Detections.random(keypoints='dense', segmentations=True).scale(1000)
            >>> # xdoctest:+REQUIRES(--show)
            >>> import kwplot
            >>> kwplot.autompl()
            >>> dets.draw(setlim=True)
        """
        import kwimage
        import kwarray
        rng = kwarray.ensure_rng(rng)
        boxes = kwimage.Boxes.random(num=num, rng=rng)
        if isinstance(classes, int):
            num_classes = classes
            classes = ['class_{}'.format(c) for c in range(classes)]
            # hack: ensure that we have a background class
            classes.append('background')
        else:
            num_classes = len(classes)
        scores = rng.rand(len(boxes))
        class_idxs = rng.randint(0, num_classes, size=len(boxes))
        self = cls(boxes=boxes, scores=scores, class_idxs=class_idxs,
                   classes=classes)
        self.meta['classes'] = classes

        if keypoints is True:
            keypoints = 'jagged'

        if segmentations:
            sseg_list = []
            for xywh in self.boxes.to_xywh().data:
                scale = xywh[2:]
                offset = xywh[0:2]
                sseg = kwimage.MultiPolygon.random(n=1, tight=True).scale(scale).translate(offset)
                sseg_list.append(sseg)
            self.data['segmentations'] = kwimage.PolygonList(sseg_list)

        if isinstance(keypoints, six.string_types):
            kp_classes = [1, 2, 3, 4]
            self.meta['kp_classes'] = kp_classes
            if keypoints == 'jagged':
                kpts_list = kwimage.PointsList([
                    kwimage.Points.random(
                        num=rng.randint(len(kp_classes)),
                        classes=kp_classes,
                    )
                    for _ in range(len(boxes))
                ])
                kpts_list.meta['classes'] = kp_classes
                self.data['keypoints'] = kpts_list
            elif keypoints == 'dense':
                keypoints = kwimage.Points.random(
                    num=(len(boxes), len(kp_classes)),
                    classes=kp_classes,)
                self.data['keypoints'] = keypoints

        self = self.scale(scale)

        if tensor:
            self = self.tensor()

        return self


@xdev.profile
def _dets_to_fcmaps(dets, bg_size, input_dims, bg_idx=0, pmin=0.6, pmax=1.0,
                    soft=True):
    """
    Construct semantic segmentation detection targets from annotations in
    dictionary format.

    Rasterize detections.

    Args:
        dets (kwimage.Detections):
        bg_size (tuple): size (W, H) to predict for backgrounds
        input_dims (tuple): window H, W

    Returns:
        dict: with keys
            size : 2D ndarray containing the W,H of the object
            dxdy : 2D ndarray containing the x,y offset of the object
            cidx : 2D ndarray containing the class index of the object

    Ignore:
        import xdev
        globals().update(xdev.get_func_kwargs(_dets_to_fcmaps))

    Example:
        >>> # xdoctest: +REQUIRES(module:ndsampler)
        >>> from kwimage.structs.detections import *  # NOQA
        >>> from kwimage.structs.detections import _dets_to_fcmaps
        >>> import kwimage
        >>> import ndsampler
        >>> sampler = ndsampler.CocoSampler.demo('photos')
        >>> iminfo, anns = sampler.load_image_with_annots(1)
        >>> image = iminfo['imdata']
        >>> input_dims = image.shape[0:2]
        >>> kp_classes = sampler.dset.keypoint_categories()
        >>> dets = kwimage.Detections.from_coco_annots(
        >>>     anns, sampler.dset.dataset['categories'],
        >>>     sampler.catgraph, kp_classes, shape=input_dims)
        >>> bg_size = [100, 100]
        >>> bg_idxs = sampler.catgraph.index('background')
        >>> fcn_target = _dets_to_fcmaps(dets, bg_size, input_dims, bg_idxs)
        >>> fcn_target.keys()
        >>> print('fcn_target: ' + ub.repr2(ub.map_vals(lambda x: x.shape, fcn_target), nl=1))
        fcn_target: {
            'cidx': (512, 512),
            'class_probs': (10, 512, 512),
            'dxdy': (2, 512, 512),
            'kpts': (2, 7, 512, 512),
            'kpts_ignore': (7, 512, 512),
            'size': (2, 512, 512),
        }
        >>> # xdoctest: +REQUIRES(--show)
        >>> import kwplot
        >>> kwplot.autompl()
        >>> size_mask = fcn_target['size']
        >>> dxdy_mask = fcn_target['dxdy']
        >>> cidx_mask = fcn_target['cidx']
        >>> kpts_mask = fcn_target['kpts']
        >>> def _vizmask(dxdy_mask):
        >>>     dx, dy = dxdy_mask
        >>>     mag = np.sqrt(dx ** 2 + dy ** 2)
        >>>     mag /= (mag.max() + 1e-9)
        >>>     mask = (cidx_mask != 0).astype(np.float32)
        >>>     angle = np.arctan2(dy, dx)
        >>>     orimask = kwplot.make_orimask(angle, mask, alpha=mag)
        >>>     vecmask = kwplot.make_vector_field(
        >>>         dx, dy, stride=4, scale=0.1, thickness=1, tipLength=.2,
        >>>         line_type=16)
        >>>     return [vecmask, orimask]
        >>> vecmask, orimask = _vizmask(dxdy_mask)
        >>> raster = kwimage.overlay_alpha_layers(
        >>>     [vecmask, orimask, image], keepalpha=False)
        >>> raster = dets.draw_on((raster * 255).astype(np.uint8),
        >>>                       labels=True, alpha=None)
        >>> kwplot.imshow(raster)
        >>> kwplot.show_if_requested()

        raster = (kwimage.overlay_alpha_layers(_vizmask(kpts_mask[:, 5]) + [image], keepalpha=False) * 255).astype(np.uint8)
        kwplot.imshow(raster, pnum=(1, 3, 2), fnum=1)
        raster = (kwimage.overlay_alpha_layers(_vizmask(kpts_mask[:, 6]) + [image], keepalpha=False) * 255).astype(np.uint8)
        kwplot.imshow(raster, pnum=(1, 3, 3), fnum=1)
        raster = (kwimage.overlay_alpha_layers(_vizmask(dxdy_mask) + [image], keepalpha=False) * 255).astype(np.uint8)
        raster = dets.draw_on(raster, labels=True, alpha=None)
        kwplot.imshow(raster, pnum=(1, 3, 1), fnum=1)
        raster = kwimage.overlay_alpha_layers(
            [vecmask, orimask, image], keepalpha=False)
        raster = dets.draw_on((raster * 255).astype(np.uint8),
                              labels=True, alpha=None)
        kwplot.imshow(raster)
        kwplot.show_if_requested()
    """
    import cv2
    # In soft mode we made a one-channel segmentation target mask
    cidx_mask = np.full(input_dims, dtype=np.int32, fill_value=bg_idx)
    if soft:
        # In soft mode we add per-class channel probability blips
        num_obj_classes = len(dets.classes)
        cidx_probs = np.full((num_obj_classes,) + tuple(input_dims),
                             dtype=np.float32, fill_value=0)

    size_mask = np.empty((2,) + tuple(input_dims), dtype=np.float32)
    size_mask[:] = np.array(bg_size)[:, None, None]

    dxdy_mask = np.zeros((2,) + tuple(input_dims), dtype=np.float32)

    dets = dets.numpy()

    cxywh = dets.boxes.to_cxywh().data
    class_idxs = dets.class_idxs
    import kwimage

    if 'segmentations' in dets.data:
        sseg_list = [None if p is None else p.to_mask(input_dims)
                     for p in dets.data['segmentations']]
    else:
        sseg_list = [None] * len(dets)

    kpts_mask = None
    if 'keypoints' in dets.data:
        kp_classes = None
        if 'classes' in dets.data['keypoints'].meta:
            kp_classes = dets.data['keypoints'].meta['classes']
        else:
            for kp in dets.data['keypoints']:
                if kp is not None and 'classes' in kp.meta:
                    kp_classes = kp.meta['classes']
                    break

        if kp_classes is not None:
            num_kp_classes = len(kp_classes)
            kpts_mask = np.zeros((2, num_kp_classes) + tuple(input_dims),
                                 dtype=np.float32)

        pts_list = dets.data['keypoints'].data
        for pts in pts_list:
            if pts is not None:
                pass

        kpts_ignore_mask = np.ones((num_kp_classes,) + tuple(input_dims),
                                   dtype=np.float32)
    else:
        pts_list = [None] * len(dets)

    # Overlay smaller classes on top of larger ones
    if len(cxywh):
        area = cxywh[..., 2] * cxywh[..., 2]
    else:
        area = []
    sortx = np.argsort(area)[::-1]
    cxywh = cxywh[sortx]
    class_idxs = class_idxs[sortx]
    pts_list = list(ub.take(pts_list, sortx))
    sseg_list = list(ub.take(sseg_list, sortx))

    def iround(x):
        return int(round(x))

    H, W = input_dims
    xcoord, ycoord = np.meshgrid(np.arange(W), np.arange(H))

    for box, cidx, sseg_mask, pts in zip(cxywh, class_idxs, sseg_list, pts_list):
        (cx, cy, w, h) = box
        center = (iround(cx), iround(cy))
        # Adjust so smaller objects get more pixels
        wf = min(1, (w / 64))
        hf = min(1, (h / 64))
        # wf = min(1, (w / W))
        # hf = min(1, (h / H))
        wf = (1 - wf) * pmax + wf * pmin
        hf = (1 - hf) * pmax + hf * pmin
        half_w = iround(wf * w / 2 + 1)
        half_h = iround(hf * h / 2 + 1)
        axes = (half_w, half_h)

        if sseg_mask is None:
            mask = np.zeros_like(cidx_mask, dtype=np.uint8)
            mask = cv2.ellipse(mask, center, axes, angle=0.0,
                               startAngle=0.0, endAngle=360.0, color=1,
                               thickness=-1).astype(np.bool)
        else:
            mask = sseg_mask.to_c_mask().data.astype(np.bool)
        # class index
        cidx_mask[mask] = int(cidx)
        if soft:
            blip = kwimage.gaussian_patch((half_h * 2, half_w * 2))
            blip = blip / blip.max()
            subindex = (slice(cy - half_h, cy + half_h),
                        slice(cx - half_w, cx + half_w))
            kwimage.subpixel_maximum(cidx_probs[cidx], blip, subindex)

        # object size
        size_mask[0][mask] = float(w)
        size_mask[1][mask] = float(h)

        assert np.all(size_mask[0][mask] == float(w))

        # object offset
        dx = cx - xcoord[mask]
        dy = cy - ycoord[mask]
        dxdy_mask[0][mask] = dx
        dxdy_mask[1][mask] = dy

        if kpts_mask is not None:

            if pts is not None:
                # Keypoint offsets
                for xy, kp_cidx in zip(pts.data['xy'].data, pts.data['class_idxs']):
                    kp_x, kp_y = xy
                    kp_dx = kp_x - xcoord[mask]
                    kp_dy = kp_y - ycoord[mask]
                    kpts_mask[0, kp_cidx][mask] = kp_dx
                    kpts_mask[1, kp_cidx][mask] = kp_dy
                    kpts_ignore_mask[kp_cidx][mask] = 0

        # SeeAlso:
        # ~/code/ovharn/ovharn/models/mcd_coder.py

    fcn_target = {
        'size': size_mask,
        'dxdy': dxdy_mask,
        'cidx': cidx_mask,
    }
    if soft:
        nonbg_idxs = sorted(set(range(num_obj_classes)) - {bg_idx})
        cidx_probs[bg_idx] = 1 - cidx_probs[nonbg_idxs].sum(axis=0)
        fcn_target['class_probs'] = cidx_probs

    if kpts_mask is not None:
        fcn_target['kpts'] = kpts_mask
        fcn_target['kpts_ignore'] = kpts_ignore_mask
    else:
        if 'keypoints' in dets.data:
            if any(kp is not None for kp in dets.data['keypoints']):
                raise AssertionError(
                    'dets had keypoints, but we didnt encode them, were the kp classes missing?')

    return fcn_target


if __name__ == '__main__':
    """
    CommandLine:
        xdoctest -m kwimage.structs.detections
    """
    import xdoctest
    xdoctest.doctest_module(__file__)
