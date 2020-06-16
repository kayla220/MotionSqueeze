import argparse
import logging

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

multiply_adds = 1


def zero_ops(m, x, y):
    m.total_ops += torch.Tensor([int(0)])


def count_convNd(m, x, y):
    x = x[0]

    kernel_ops = m.weight.size()[2:].numel()  # Kw x Kh
    bias_ops = 1 if m.bias is not None else 0

    # N x Cout x H x W x  (Cin x Kw x Kh + bias)
    total_ops = y.nelement() * (m.in_channels // m.groups * kernel_ops + bias_ops)

    m.total_ops += torch.Tensor([int(total_ops)])


def count_convNd_ver2(m, x, y):
    x = x[0]

    # N x H x W (exclude Cout)
    output_size = (y.size()[:1] + y.size()[2:]).numel()
    # Cout x Cin x Kw x Kh
    kernel_ops = m.weight.nelement()
    if m.bias is not None:
        # Cout x 1
        kernel_ops += + m.bias.nelement()
    # x N x H x W x Cout x (Cin x Kw x Kh + bias)
    m.total_ops += torch.Tensor([int(output_size * kernel_ops)])


def count_bn(m, x, y):
    x = x[0]

    nelements = x.numel()
    # subtract, divide, gamma, beta
    total_ops = 4 * nelements

    m.total_ops += torch.Tensor([int(total_ops)])


def count_relu(m, x, y):
    x = x[0]

    nelements = x.numel()

    m.total_ops += torch.Tensor([int(nelements)])


def count_softmax(m, x, y):
    x = x[0]
    #print("softmax x.size(): " + str(x.size()))
    batch_size, nfeatures, _ = x.size()

    total_exp = nfeatures
    total_add = nfeatures - 1
    total_div = nfeatures
    total_ops = batch_size * (total_exp + total_add + total_div)

    m.total_ops += torch.Tensor([int(total_ops)])


def count_avgpool(m, x, y):
    total_add = torch.prod(torch.Tensor([m.kernel_size]))
    total_div = 1
    kernel_ops = total_add + total_div
    num_elements = y.numel()
    total_ops = kernel_ops * num_elements

    m.total_ops += torch.Tensor([int(total_ops)])


def count_adap_avgpool(m, x, y):
    kernel = torch.Tensor([*(x[0].shape[2:])]) // torch.Tensor(list((m.output_size,))).squeeze()
    total_add = torch.prod(kernel)
    total_div = 1
    kernel_ops = total_add + total_div
    num_elements = y.numel()
    total_ops = kernel_ops * num_elements

    m.total_ops += torch.Tensor([int(total_ops)])

# TODO: verify the accuracy
def count_upsample(m, x, y):
    if m.mode not in ("nearest", "linear", "bilinear", "bicubic", ): #"trilinear"
        logger.warning("mode %s is not implemented yet, take it a zero op" % m.mode)
        return zero_ops(m, x, y)

    if m.mode == "nearest":
        return zero_ops(m, x, y)

    x = x[0]
    if m.mode == "linear":
        total_ops = y.nelement() * 5 # 2 muls + 3 add
    elif m.mode == "bilinear":
        # https://en.wikipedia.org/wiki/Bilinear_interpolation
        total_ops = y.nelement() * 13 # 6 muls + 7 adds
    elif m.mode == "bicubic":
        # https://en.wikipedia.org/wiki/Bicubic_interpolation
        # Product matrix [4x4] x [4x4] x [4x4]
        ops_solve_A = 224 # 128 muls + 96 adds
        ops_solve_p = 35 # 16 muls + 12 adds + 4 muls + 3 adds
        total_ops = y.nelement() * (ops_solve_A + ops_solve_p)
    elif m.mode == "trilinear":
        # https://en.wikipedia.org/wiki/Trilinear_interpolation
        # can viewed as 2 bilinear + 1 linear
        total_ops = y.nelement() * (13 * 2 + 5)

    m.total_ops += torch.Tensor([int(total_ops)])


def count_linear(m, x, y):
    # per output element
    total_mul = m.in_features
    total_add = m.in_features - 1
    num_elements = y.numel()
    total_ops = (total_mul + total_add) * num_elements

    m.total_ops += torch.Tensor([int(total_ops)])

########custom count functions#########

def count_sigmoid(m, x, y):
    x = x[0]

    nelements = x.numel()
    total_ops = nelements * 4
    m.total_ops += torch.Tensor([int(total_ops)])
    
def count_matching(m, x, y):
    b, c, h1, w1 = x[0].size()
    b, c, h2, w2 = x[1].size()
    ops_per_instance = 2*c - 1
    nelements1 = h1*w1
    nelements2 = h2*w2
    total_ops = b * nelements1 * nelements2 * ops_per_instance
    m.total_ops += torch.Tensor([int(total_ops)])
    
def count_matching2(m, x, y):
    #assume padding == (ks-1)/2
    ks = m.kernel_size
    p = m.patch_size
    pad = m.padding
    s1 = m.stride
    s2 = m.dilation_patch
    #print(x[0].size())
    b, c, h1, w1 = x[0].size()
    b, c, h2, w2 = x[1].size()
    ops_per_instance = 2*c*(ks**2) - 1
    nelements = (h1//s1)*(w2//s1)
    total_ops = b * nelements * ops_per_instance * (p ** 2)
    m.total_ops += torch.Tensor([int(total_ops)])

def count_matching3(m, x, y):
    ks = m.kernel_size
    p = m.patch_size
    pad = m.padding
    s1 = m.stride
    s2 = m.dilation_patch
    b,c,h1,w1 = x[0].size()
    ops_per_instance = 2*c*(ks**2)-1
    total_ops = y.numel()*ops_per_instance
    m.total_ops += torch.Tensor([int(total_ops)])
    
def count_repflow(m, x, y):
    def _count_forward_grad(x_):
        kernel_size = m.f_grad.size()[1:].numel()
        return x_.numel()*kernel_size*2
    
    def _count_divergence(x_):
        kernel_size = m.div.size()[1:].numel()
        return x_.numel()*(kernel_size*2+1)
    
    inp = x[0]
    total_ops = inp.numel()*2 # normalize
    
    b,c,t,h,w = inp.size()
    x = inp[:,:,:-1]
    y = inp[:,:,1:]
    b,c,t,h,w = x.size()
    print(x.size())
    
    total_ops += 2 # L_t, taut
    
    kernel_size = m.img_grad.size()[1:].numel()
    total_ops += x.numel()*kernel_size*2 # grad2_x, grad2_y
    total_ops += x.numel()*(2+2+5) # gsqx, gsqy, grad, rho_c
    
    ## for loop starts
    loop_ops = x.numel()*(5+6+2)
    loop_ops += 2*(x.numel()*2 + _count_divergence(x)) # u1, u2 divergence
    loop_ops += 2*_count_forward_grad(x) # forward_grad
    loop_ops += 4*x.numel()*9 # p11,p12,p21,p22
    total_ops += m.n_iter * loop_ops
    print(_count_divergence(x))
    m.total_ops += torch.Tensor([int(total_ops)])
    print(m.total_ops)
